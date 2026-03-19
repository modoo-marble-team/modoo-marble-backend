from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any

import socketio
import structlog

from app.config import settings
from app.game.enums import PlayerState, ServerEventType
from app.game.models import GameState, PlayerGameState
from app.game.patch import op_set
from app.game.presentation import serialize_game_patch
from app.game.rules import PHASE_GAME_OVER, PHASE_WAIT_ROLL
from app.game.state import delete_game_state, game_lock, get_game_state, save_game_state
from app.game.timer import cancel_turn_timer
from app.presence import update_status
from app.redis_client import get_redis
from app.services.room_service import RoomService

logger = structlog.get_logger()

_LEADER_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class GameSyncRuntime:
    def __init__(self, sio: socketio.AsyncServer) -> None:
        self._sio = sio
        self._user_sids: dict[int, set[str]] = {}
        self._scheduler_task: asyncio.Task[None] | None = None
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._disconnect_queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue(
            maxsize=settings.GAME_SYNC_WORKER_CONCURRENCY
            * settings.GAME_SYNC_WORKER_BATCH_SIZE
        )
        self._instance_id = str(uuid.uuid4())
        self._leader_script: Any = None

    def _patchlog_key(self, game_id: str) -> str:
        return f"game:{game_id}:patchlog"

    def _disconnected_at_key(self, game_id: str, player_id: int) -> str:
        return f"game:{game_id}:player:{player_id}:disconnected_at"

    def _active_game_key(self, user_id: int) -> str:
        return f"game:user:{user_id}:active"

    def _legacy_user_game_key(self, user_id: int) -> str:
        return f"user:{user_id}:game"

    def _disconnect_schedule_key(self, shard: int) -> str:
        return f"game:disconnect_schedule:{shard}"

    def _disconnect_schedule_member(self, game_id: str, player_id: int) -> str:
        return json.dumps(
            {"gameId": game_id, "playerId": player_id},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _parse_disconnect_schedule_member(self, member: str) -> tuple[str, int] | None:
        try:
            payload = json.loads(member)
            return str(payload["gameId"]), int(payload["playerId"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _timer_claim_key(self, game_id: str, player_id: int) -> str:
        return f"game:{game_id}:player:{player_id}:disconnect_claim"

    def _leader_key(self) -> str:
        return "game:disconnect_scheduler:leader"

    def _schedule_shard(self, game_id: str, player_id: int) -> int:
        shard_count = max(settings.GAME_SYNC_DISCONNECT_SCHEDULE_SHARDS, 1)
        digest = hashlib.md5(f"{game_id}:{player_id}".encode()).hexdigest()
        return int(digest, 16) % shard_count

    def _now_ts(self) -> float:
        return time.time()

    async def handle_connect(self, *, sid: str, user_id: int) -> None:
        self._user_sids.setdefault(user_id, set()).add(sid)

    async def handle_disconnect(self, *, sid: str, user_id: int) -> None:
        sids = self._user_sids.get(user_id)
        if sids is not None:
            sids.discard(sid)
            if not sids:
                self._user_sids.pop(user_id, None)

        if user_id in self._user_sids:
            return

        game_id = await self.get_active_game(user_id=user_id)
        if not game_id:
            return

        state = await get_game_state(game_id)
        if state is None or state.status != "playing":
            return

        player = state.player(user_id)
        if player is None or player.player_state == PlayerState.BANKRUPT:
            return

        await self.set_disconnected_at(game_id=game_id, player_id=user_id)
        await self._sio.emit(
            "game:patch",
            {
                "gameId": game_id,
                "revision": state.revision,
                "turn": state.turn,
                "events": [
                    {
                        "type": ServerEventType.PLAYER_DISCONNECTED,
                        "playerId": user_id,
                        "timeoutSeconds": settings.GAME_SYNC_DISCONNECT_GRACE_SECONDS,
                    }
                ],
                "patch": [],
                "snapshot": None,
            },
            room=f"game:{game_id}",
        )

    async def handle_sync(
        self,
        *,
        sid: str,
        user_id: int,
        game_id: str,
        known_revision: int,
    ) -> GameState | None:
        state = await get_game_state(game_id)
        if state is None:
            await self._emit_desync(
                sid=sid,
                game_id=game_id,
                message="진행 중인 게임 상태를 찾을 수 없습니다.",
                snapshot=None,
            )
            return None

        if user_id not in state.players:
            await self._emit_desync(
                sid=sid,
                game_id=game_id,
                message="게임 참가자가 아닙니다.",
                snapshot=None,
            )
            return None

        await self.set_active_game(user_id=user_id, game_id=game_id)
        was_disconnected = (
            await self.get_disconnected_at(game_id=game_id, player_id=user_id)
        ) is not None
        await self.clear_disconnected_at(game_id=game_id, player_id=user_id)
        await self._sio.enter_room(sid, f"game:{game_id}")
        await update_status(user_id=str(user_id), status="playing")

        current_revision = state.revision
        sync_event = {
            "type": ServerEventType.SYNCED,
            "player_id": user_id,
            "known_revision": known_revision,
            "current_revision": current_revision,
        }

        if known_revision < 0:
            await self._sio.emit(
                "game:patch",
                serialize_game_patch(
                    state,
                    events=[sync_event],
                    patches=[],
                    include_snapshot=True,
                ),
                to=sid,
            )
            await self._emit_reconnected_if_needed(
                game_id=game_id,
                player_id=user_id,
                was_disconnected=was_disconnected,
            )
            return state

        if known_revision > current_revision:
            snapshot_packet = serialize_game_patch(
                state,
                events=[
                    {
                        **sync_event,
                        "require_full_reload": True,
                        "snapshot_revision": current_revision,
                    }
                ],
                patches=[],
                include_snapshot=True,
            )
            await self._emit_desync(
                sid=sid,
                game_id=game_id,
                message="클라이언트 상태가 서버보다 앞서 있습니다.",
                snapshot=snapshot_packet,
            )
            await self._emit_reconnected_if_needed(
                game_id=game_id,
                player_id=user_id,
                was_disconnected=was_disconnected,
            )
            return state

        if known_revision == current_revision:
            include_snapshot = current_revision == 0
            await self._sio.emit(
                "game:patch",
                serialize_game_patch(
                    state,
                    events=[sync_event],
                    patches=[],
                    include_snapshot=include_snapshot,
                ),
                to=sid,
            )
            await self._emit_reconnected_if_needed(
                game_id=game_id,
                player_id=user_id,
                was_disconnected=was_disconnected,
            )
            return state

        packets = await self.get_patches_after(
            game_id=game_id,
            known_revision=known_revision,
        )
        if self._has_contiguous_packets(
            packets=packets,
            start_revision=known_revision + 1,
            end_revision=current_revision,
        ):
            for packet in packets:
                await self._sio.emit("game:patch", packet, to=sid)
            await self._emit_reconnected_if_needed(
                game_id=game_id,
                player_id=user_id,
                was_disconnected=was_disconnected,
            )
            return state

        snapshot_packet = serialize_game_patch(
            state,
            events=[
                {
                    **sync_event,
                    "require_full_reload": True,
                    "snapshot_revision": current_revision,
                }
            ],
            patches=[],
            include_snapshot=True,
        )
        await self._sio.emit("game:patch", snapshot_packet, to=sid)
        await self._emit_reconnected_if_needed(
            game_id=game_id,
            player_id=user_id,
            was_disconnected=was_disconnected,
        )
        return state

    async def build_and_store_patch_packet(
        self,
        *,
        state: GameState,
        events: list[dict[str, Any]],
        patches: list[dict[str, Any]],
        include_snapshot: bool = False,
    ) -> dict[str, Any]:
        packet = serialize_game_patch(
            state,
            events=events,
            patches=patches,
            include_snapshot=include_snapshot,
        )
        await self.append_patch_packet(game_id=state.game_id, packet=packet)
        return packet

    async def append_patch_packet(
        self,
        *,
        game_id: str,
        packet: dict[str, Any],
    ) -> None:
        redis = get_redis()
        key = self._patchlog_key(game_id)
        revision = int(packet["revision"])
        member = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))

        await redis.zadd(key, {member: revision})

        count = await redis.zcard(key)
        overflow = count - settings.GAME_SYNC_PATCH_KEEP_COUNT
        if overflow > 0:
            await redis.zremrangebyrank(key, 0, overflow - 1)

    async def get_patches_after(
        self,
        *,
        game_id: str,
        known_revision: int,
    ) -> list[dict[str, Any]]:
        redis = get_redis()
        raw_packets = await redis.zrangebyscore(
            self._patchlog_key(game_id),
            min=known_revision + 1,
            max="+inf",
        )
        packets = [json.loads(raw) for raw in raw_packets]
        packets.sort(key=lambda item: int(item.get("revision", 0)))
        return packets

    async def set_active_game(self, *, user_id: int, game_id: str) -> None:
        redis = get_redis()
        await redis.set(self._active_game_key(user_id), game_id)

    async def get_active_game(self, *, user_id: int) -> str | None:
        redis = get_redis()
        return await redis.get(self._active_game_key(user_id))

    async def clear_active_game(self, *, user_id: int) -> None:
        redis = get_redis()
        await redis.delete(self._active_game_key(user_id))

    async def set_disconnected_at(self, *, game_id: str, player_id: int) -> None:
        redis = get_redis()
        disconnected_at = self._now_ts()
        due_at = disconnected_at + settings.GAME_SYNC_DISCONNECT_GRACE_SECONDS
        member = self._disconnect_schedule_member(game_id, player_id)
        shard = self._schedule_shard(game_id, player_id)

        await redis.set(
            self._disconnected_at_key(game_id, player_id),
            str(disconnected_at),
            ex=settings.GAME_SYNC_DISCONNECT_GRACE_SECONDS,
        )
        await redis.zadd(self._disconnect_schedule_key(shard), {member: due_at})

    async def clear_disconnected_at(self, *, game_id: str, player_id: int) -> None:
        redis = get_redis()
        member = self._disconnect_schedule_member(game_id, player_id)
        shard = self._schedule_shard(game_id, player_id)

        await redis.delete(self._disconnected_at_key(game_id, player_id))
        await redis.zrem(self._disconnect_schedule_key(shard), member)
        await redis.delete(self._timer_claim_key(game_id, player_id))

    async def get_disconnected_at(
        self,
        *,
        game_id: str,
        player_id: int,
    ) -> float | None:
        redis = get_redis()
        raw = await redis.get(self._disconnected_at_key(game_id, player_id))
        if raw is None:
            return None
        return float(raw)

    async def start_scheduler(self) -> None:
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return

        await self._cleanup_expired_disconnects()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self._worker_tasks = [
            asyncio.create_task(self._worker_loop())
            for _ in range(settings.GAME_SYNC_WORKER_COUNT)
        ]

    async def stop_scheduler(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        for task in self._worker_tasks:
            task.cancel()
        for task in self._worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []

        while not self._disconnect_queue.empty():
            try:
                self._disconnect_queue.get_nowait()
                self._disconnect_queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def _emit_desync(
        self,
        *,
        sid: str,
        game_id: str,
        message: str,
        snapshot: dict[str, Any] | None,
    ) -> None:
        await self._sio.emit(
            "game:error",
            {"gameId": game_id, "code": "DESYNC", "message": message},
            to=sid,
        )
        if snapshot is not None:
            await self._sio.emit("game:patch", snapshot, to=sid)

    async def _emit_reconnected_if_needed(
        self,
        *,
        game_id: str,
        player_id: int,
        was_disconnected: bool,
    ) -> None:
        if not was_disconnected:
            return

        state = await get_game_state(game_id)
        await self._sio.emit(
            "game:patch",
            {
                "gameId": game_id,
                "revision": state.revision if state else None,
                "turn": state.turn if state else None,
                "events": [
                    {
                        "type": ServerEventType.PLAYER_RECONNECTED,
                        "playerId": player_id,
                    }
                ],
                "patch": [],
                "snapshot": None,
            },
            room=f"game:{game_id}",
        )

    def _has_contiguous_packets(
        self,
        *,
        packets: list[dict[str, Any]],
        start_revision: int,
        end_revision: int,
    ) -> bool:
        if not packets:
            return False

        expected = start_revision
        for packet in packets:
            revision = int(packet.get("revision", 0))
            if revision != expected:
                return False
            expected += 1
        return expected - 1 == end_revision

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                if await self._acquire_leader():
                    await self._drain_due_disconnects()
                await asyncio.sleep(settings.GAME_SYNC_WORKER_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("game sync scheduler loop error", error=str(exc))
                await asyncio.sleep(settings.GAME_SYNC_WORKER_POLL_INTERVAL_SECONDS)

    async def _worker_loop(self) -> None:
        while True:
            try:
                game_id, player_id = await self._disconnect_queue.get()
                try:
                    await self._process_due_disconnect(
                        game_id=game_id,
                        player_id=player_id,
                    )
                finally:
                    self._disconnect_queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("disconnect worker loop error", error=str(exc))

    async def _acquire_leader(self) -> bool:
        redis = get_redis()
        key = self._leader_key()

        if self._leader_script is None:
            self._leader_script = redis.register_script(_LEADER_RENEW_LUA)

        current = await redis.get(key)
        if current == self._instance_id:
            renewed = await self._leader_script(
                keys=[key],
                args=[self._instance_id, settings.GAME_SYNC_LEADER_TTL_SECONDS],
            )
            return bool(renewed)

        acquired = await redis.set(
            key,
            self._instance_id,
            nx=True,
            ex=settings.GAME_SYNC_LEADER_TTL_SECONDS,
        )
        return bool(acquired)

    async def _drain_due_disconnects(self) -> None:
        redis = get_redis()
        now = self._now_ts()

        for shard in range(settings.GAME_SYNC_DISCONNECT_SCHEDULE_SHARDS):
            key = self._disconnect_schedule_key(shard)
            members = await redis.zrangebyscore(
                key,
                min="-inf",
                max=now,
                start=0,
                num=settings.GAME_SYNC_WORKER_BATCH_SIZE,
            )

            for raw_member in members:
                parsed = self._parse_disconnect_schedule_member(str(raw_member))
                if parsed is None:
                    await redis.zrem(key, raw_member)
                    continue

                game_id, player_id = parsed
                try:
                    self._disconnect_queue.put_nowait((game_id, player_id))
                except asyncio.QueueFull:
                    logger.warning(
                        "disconnect queue is full",
                        game_id=game_id,
                        player_id=player_id,
                    )
                    return

    async def _cleanup_expired_disconnects(self) -> None:
        redis = get_redis()
        cutoff = self._now_ts() - settings.GAME_SYNC_DISCONNECT_GRACE_SECONDS

        for shard in range(settings.GAME_SYNC_DISCONNECT_SCHEDULE_SHARDS):
            await redis.zremrangebyscore(
                self._disconnect_schedule_key(shard),
                min="-inf",
                max=cutoff,
            )

    async def _try_claim_timer(self, *, game_id: str, player_id: int) -> bool:
        redis = get_redis()
        claimed = await redis.set(
            self._timer_claim_key(game_id, player_id),
            self._instance_id,
            nx=True,
            ex=settings.GAME_SYNC_TIMER_CLAIM_TTL_SECONDS,
        )
        return bool(claimed)

    async def _process_due_disconnect(self, *, game_id: str, player_id: int) -> None:
        disconnected_at = await self.get_disconnected_at(
            game_id=game_id,
            player_id=player_id,
        )
        if disconnected_at is None:
            await self.clear_disconnected_at(game_id=game_id, player_id=player_id)
            return

        if (
            self._now_ts() - disconnected_at
            < settings.GAME_SYNC_DISCONNECT_GRACE_SECONDS
        ):
            return

        async with game_lock(game_id):
            disconnected_at = await self.get_disconnected_at(
                game_id=game_id,
                player_id=player_id,
            )
            if disconnected_at is None:
                await self.clear_disconnected_at(game_id=game_id, player_id=player_id)
                return

            if (
                self._now_ts() - disconnected_at
                < settings.GAME_SYNC_DISCONNECT_GRACE_SECONDS
            ):
                return

            if not await self._try_claim_timer(game_id=game_id, player_id=player_id):
                return

            try:
                state = await get_game_state(game_id)
                if state is None or state.status != "playing":
                    await self.clear_disconnected_at(
                        game_id=game_id, player_id=player_id
                    )
                    return

                player = state.player(player_id)
                if player is None or player.player_state == PlayerState.BANKRUPT:
                    await self.clear_disconnected_at(
                        game_id=game_id, player_id=player_id
                    )
                    return

                events: list[dict[str, Any]] = []
                patch: list[dict[str, Any]] = []

                self._bankrupt_player(
                    state=state,
                    player_id=player_id,
                    events=events,
                    patch=patch,
                    reason="disconnect_timeout",
                )

                alive_players = self._active_players(state)
                if len(alive_players) <= 1:
                    winner = (
                        self._winner_payload(alive_players[0])
                        if alive_players
                        else None
                    )
                    state.status = "finished"
                    state.phase = PHASE_GAME_OVER
                    state.pending_prompt = None
                    state.winner_id = winner["playerId"] if winner else None
                    patch.extend(
                        [
                            op_set("status", "finished"),
                            op_set("phase", PHASE_GAME_OVER),
                            op_set("pending_prompt", None),
                            op_set("winner_id", state.winner_id),
                        ]
                    )
                    events.append(
                        {
                            "type": ServerEventType.GAME_OVER,
                            "reason": "disconnect_timeout",
                            "winner": winner,
                        }
                    )
                elif state.current_player_id == player_id:
                    self._advance_turn_after_forced_bankruptcy(
                        state=state,
                        player_id=player_id,
                        events=events,
                        patch=patch,
                    )

                state.revision += 1
                await save_game_state(game_id, state)

                packet = await self.build_and_store_patch_packet(
                    state=state,
                    events=events,
                    patches=patch,
                    include_snapshot=False,
                )

                await self.clear_disconnected_at(game_id=game_id, player_id=player_id)
                await self._sio.emit("game:patch", packet, room=f"game:{game_id}")
                if state.status == "finished":
                    for pid in state.players:
                        await update_status(user_id=str(pid), status="lobby")
                    if self._all_game_players_offline(state):
                        await self._cleanup_abandoned_game_room(state)
            finally:
                redis = get_redis()
                await redis.delete(self._timer_claim_key(game_id, player_id))

    def _bankrupt_player(
        self,
        *,
        state: GameState,
        player_id: int,
        events: list[dict[str, Any]],
        patch: list[dict[str, Any]],
        reason: str,
    ) -> None:
        player = state.require_player(player_id)
        player.balance = 0
        player.player_state = PlayerState.BANKRUPT
        player.state_duration = 0
        player.consecutive_doubles = 0
        player.building_levels = {}

        patch.extend(
            [
                op_set(f"players.{player_id}.balance", 0),
                op_set(f"players.{player_id}.player_state", PlayerState.BANKRUPT),
                op_set(f"players.{player_id}.state_duration", 0),
                op_set(f"players.{player_id}.consecutive_doubles", 0),
                op_set(f"players.{player_id}.building_levels", {}),
            ]
        )

        for tile_id in list(player.owned_tiles):
            tile_state = state.tile(tile_id)
            if tile_state is None:
                continue
            tile_state.owner_id = None
            tile_state.building_level = 0
            patch.extend(
                [
                    op_set(f"tiles.{tile_id}.owner_id", None),
                    op_set(f"tiles.{tile_id}.building_level", 0),
                ]
            )

        player.owned_tiles = []
        patch.append(op_set(f"players.{player_id}.owned_tiles", []))

        pending_prompt = state.pending_prompt
        if pending_prompt is not None and pending_prompt.player_id == player_id:
            state.pending_prompt = None
            patch.append(op_set("pending_prompt", None))

        events.append(
            {
                "type": ServerEventType.PLAYER_STATE_CHANGED,
                "player_id": player_id,
                "state": PlayerState.BANKRUPT,
                "reason": reason,
            }
        )

    def _advance_turn_after_forced_bankruptcy(
        self,
        *,
        state: GameState,
        player_id: int,
        events: list[dict[str, Any]],
        patch: list[dict[str, Any]],
    ) -> None:
        active_players = self._active_players(state)
        if not active_players:
            state.status = "finished"
            state.phase = PHASE_GAME_OVER
            state.pending_prompt = None
            state.winner_id = None
            patch.extend(
                [
                    op_set("status", "finished"),
                    op_set("phase", PHASE_GAME_OVER),
                    op_set("pending_prompt", None),
                    op_set("winner_id", None),
                ]
            )
            events.append(
                {
                    "type": ServerEventType.GAME_OVER,
                    "reason": "disconnect_timeout",
                    "winner": None,
                }
            )
            return

        current_order = state.require_player(player_id).turn_order
        next_player = active_players[0]
        for candidate in active_players:
            if candidate.turn_order > current_order:
                next_player = candidate
                break

        next_player_id = next_player.player_id
        next_order = next_player.turn_order
        new_turn = state.turn + 1
        new_round = state.round + 1 if next_order <= current_order else state.round

        state.current_player_id = next_player_id
        state.turn = new_turn
        state.round = new_round
        state.phase = PHASE_WAIT_ROLL
        state.pending_prompt = None

        patch.extend(
            [
                op_set("current_player_id", next_player_id),
                op_set("turn", new_turn),
                op_set("round", new_round),
                op_set("phase", PHASE_WAIT_ROLL),
                op_set("pending_prompt", None),
            ]
        )
        events.append(
            {
                "type": ServerEventType.TURN_ENDED,
                "player_id": player_id,
                "next_player_id": next_player_id,
                "turn": new_turn,
                "round": new_round,
            }
        )

    def _winner_payload(self, player: PlayerGameState) -> dict[str, Any]:
        return {
            "playerId": player.player_id,
            "nickname": player.nickname,
            "balance": player.balance,
        }

    def _active_players(self, state: GameState) -> list[PlayerGameState]:
        return state.active_players()

    def _all_game_players_offline(self, state: GameState) -> bool:
        return all(player_id not in self._user_sids for player_id in state.players)

    async def _cleanup_abandoned_game_room(self, state: GameState) -> None:
        room_service = RoomService()
        redis = get_redis()

        cancel_turn_timer(state.game_id)
        await room_service.cleanup_abandoned_room(
            room_id=state.room_id,
            player_ids=list(state.players),
        )
        await delete_game_state(state.game_id)
        await redis.delete(self._patchlog_key(state.game_id))

        for player_id in state.players:
            await self.clear_active_game(user_id=player_id)
            await redis.delete(self._legacy_user_game_key(player_id))
            await self.clear_disconnected_at(
                game_id=state.game_id,
                player_id=player_id,
            )

        await self._sio.emit(
            "lobby_updated",
            {"action": "removed", "room": {"id": state.room_id}},
        )


_runtime: GameSyncRuntime | None = None


def init_game_sync_runtime(sio: socketio.AsyncServer) -> GameSyncRuntime:
    global _runtime
    if _runtime is None:
        _runtime = GameSyncRuntime(sio)
    return _runtime


async def handle_game_socket_connect(*, sid: str, user_id: int) -> None:
    if _runtime is None:
        return
    await _runtime.handle_connect(sid=sid, user_id=user_id)


async def handle_game_socket_disconnect(*, sid: str, user_id: int) -> None:
    if _runtime is None:
        return
    await _runtime.handle_disconnect(sid=sid, user_id=user_id)


async def start_game_sync_scheduler() -> None:
    if _runtime is None:
        return
    await _runtime.start_scheduler()


async def stop_game_sync_scheduler() -> None:
    if _runtime is None:
        return
    await _runtime.stop_scheduler()
