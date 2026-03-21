"""소켓 이벤트를 받아 게임 서비스와 연결하는 모듈.

핵심 규칙 계산은 다른 계층에 맡기고,
여기서는 입출력 흐름을 조립하는 역할만 한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import socketio
import structlog

from app.game.application import (
    GameActionService,
    GameDesyncError,
    GameMembershipError,
    GameNotFoundError,
)
from app.game.errors import GameActionError
from app.game.infrastructure.socket_presenter import GameSocketPresenter
from app.game.infrastructure.state_repository import GameStateRepository
from app.game.models import GameState
from app.game.state import (
    LockAcquisitionError,
    game_lock,
    get_game_state,
    save_game_state,
)
from app.game.sync_runtime import init_game_sync_runtime
from app.game.timer import build_timer_sync_payload, start_turn_timer, sync_prompt_timer
from app.services.room_service import RoomService

logger = structlog.get_logger()

PROMPT_RESPONSE_ACK_TYPE = "PROMPT_RESPONSE"


def register_game_handlers(
    sio: socketio.AsyncServer,
    sid_to_user: dict[str, int],
) -> None:
    # 소켓 서버에 게임 관련 이벤트 핸들러를 등록한다.
    room_service = RoomService()
    presenter = GameSocketPresenter()
    sync_runtime = init_game_sync_runtime(sio)

    class SocketHandlerRepository(GameStateRepository):
        # 기존 저장 함수를 현재 서비스 인터페이스에 맞춰 연결하는 어댑터.
        async def load(self, game_id: str) -> GameState | None:
            return await get_game_state(game_id)

        async def save(self, game_id: str, state: GameState) -> None:
            await save_game_state(game_id, state)

        @asynccontextmanager
        async def lock(self, game_id: str):  # type: ignore[override]
            async with game_lock(game_id):
                yield

    action_service = GameActionService(repository=SocketHandlerRepository())

    async def emit_game_error(
        *,
        sid: str,
        code: str,
        message: str,
        game_id: str | None = None,
    ) -> None:
        """game:error 이벤트를 전송하는 헬퍼 함수."""
        await sio.emit(
            "game:error",
            {"gameId": game_id, "code": code, "message": message},
            to=sid,
        )

    async def emit_prompt_if_needed(state: GameState) -> None:
        # 현재 상태에 프롬프트가 있으면 대상 유저에게만 전송한다.
        sync_prompt_timer(game_id=state.game_id, prompt=state.pending_prompt)
        prompt_payload = presenter.serialize_prompt(state.pending_prompt)
        if not prompt_payload or state.pending_prompt is None:
            return
        await sio.emit(
            "game:prompt",
            prompt_payload,
            room=f"user:{state.pending_prompt.player_id}",
        )

    async def ensure_game_room_membership(
        *,
        sid: str,
        game_id: str,
        state: GameState,
        user_id: int,
    ) -> bool:
        # 요청한 유저가 해당 게임 방 참가자인지 확인한다.
        if user_id not in state.players:
            await emit_game_error(
                sid=sid,
                game_id=game_id,
                code="NOT_GAME_MEMBER",
                message="게임 참가자가 아닙니다.",
            )
            return False

        await sio.enter_room(sid, f"game:{game_id}")
        return True

    def build_error_ack(
        *,
        game_id: str | None,
        action_id: str,
        action_type: str | None,
        code: str,
        message: str,
        revision: int,
        prompt_id: str | None = None,
    ) -> dict:
        return {
            "gameId": game_id,
            "actionId": action_id,
            "type": action_type,
            "ok": False,
            "error": {"code": code, "message": message},
            "revision": revision,
            "promptId": prompt_id,
        }

    async def emit_desync_error(
        *,
        sid: str,
        game_id: str | None,
        message: str = "knownRevision 값이 올바르지 않습니다.",
    ) -> None:
        await emit_game_error(
            sid=sid,
            game_id=game_id,
            code="DESYNC",
            message=message,
        )

    async def parse_known_revision(
        *,
        sid: str,
        data: dict,
        game_id: str | None,
    ) -> int | None:
        if "knownRevision" not in data:
            return None

        try:
            return int(data.get("knownRevision"))
        except (TypeError, ValueError):
            await emit_desync_error(sid=sid, game_id=game_id)
            return None

    def did_turn_advance(
        state: GameState,
        *,
        previous_turn: int,
        previous_player_id: int,
    ) -> bool:
        return (
            state.turn != previous_turn or state.current_player_id != previous_player_id
        )

    @sio.on("enter_room")
    async def enter_room(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        room_id = str(data.get("room_id") or "")

        if user_id is None:
            await emit_game_error(
                sid=sid, code="AUTH_REQUIRED", message="인증이 필요합니다."
            )
            return

        room = await room_service.get_room(room_id)
        if room is None:
            await emit_game_error(
                sid=sid, code="ROOM_NOT_FOUND", message="방을 찾을 수 없습니다."
            )
            return

        if not any(player["id"] == str(user_id) for player in room["players"]):
            await emit_game_error(
                sid=sid, code="NOT_ROOM_MEMBER", message="방 멤버가 아닙니다."
            )
            return

        await sio.enter_room(sid, f"room:{room_id}")

    @sio.on("leave_room")
    async def leave_room(sid: str, data: dict) -> None:
        room_id = str(data.get("room_id") or "")
        if room_id:
            await sio.leave_room(sid, f"room:{room_id}")

    @sio.on("send_chat")
    async def send_chat(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        room_id = str(data.get("room_id") or "")
        message = str(data.get("message") or "").strip()

        if user_id is None:
            await emit_game_error(
                sid=sid, code="AUTH_REQUIRED", message="인증이 필요합니다."
            )
            return

        if not room_id or not message:
            return

        _room, chat_message = await room_service.add_chat_message(
            room_id=room_id,
            user_id=user_id,
            message=message,
        )
        await sio.emit(
            "chat",
            {"room_id": room_id, **chat_message},
            room=f"room:{room_id}",
        )

    @sio.on("game:sync")
    async def game_sync(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        game_id = str((data or {}).get("gameId") or "")
        known_revision_raw = (data or {}).get("knownRevision", -1)

        if user_id is None:
            await emit_game_error(
                sid=sid,
                game_id=game_id or None,
                code="AUTH_REQUIRED",
                message="인증이 필요합니다.",
            )
            return

        if not game_id:
            await emit_game_error(
                sid=sid,
                game_id=None,
                code="INVALID_REQUEST",
                message="gameId가 필요합니다.",
            )
            return

        try:
            known_revision = int(known_revision_raw)
        except (TypeError, ValueError):
            await emit_desync_error(sid=sid, game_id=game_id)
            return

        state = await sync_runtime.handle_sync(
            sid=sid,
            user_id=user_id,
            game_id=game_id,
            known_revision=known_revision,
        )
        if state is None:
            return

        await emit_prompt_if_needed(state)

    @sio.on("game:sync_timer")
    async def game_sync_timer(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        game_id = str((data or {}).get("gameId") or "")

        if user_id is None:
            await emit_game_error(
                sid=sid,
                game_id=game_id or None,
                code="AUTH_REQUIRED",
                message="인증이 필요합니다.",
            )
            return

        if not game_id:
            await emit_game_error(
                sid=sid,
                game_id=None,
                code="INVALID_REQUEST",
                message="gameId가 필요합니다.",
            )
            return

        state = await get_game_state(game_id)
        if state is None:
            await emit_game_error(
                sid=sid,
                game_id=game_id,
                code="GAME_NOT_FOUND",
                message="게임을 찾을 수 없습니다.",
            )
            return

        if not await ensure_game_room_membership(
            sid=sid,
            game_id=game_id,
            state=state,
            user_id=user_id,
        ):
            return

        await sio.emit(
            "game:timer_sync",
            build_timer_sync_payload(
                game_id=game_id,
                state=state,
                user_id=user_id,
            ),
            to=sid,
        )

    @sio.on("game:action")
    async def handle_game_action(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        if user_id is None:
            await emit_game_error(
                sid=sid, code="AUTH_REQUIRED", message="인증이 필요합니다."
            )
            return

        game_id = data.get("gameId")
        action_id = data.get("actionId", "")
        action_type = data.get("type")
        state = None
        events: list[dict] = []
        patches: list[dict] = []

        if not game_id or not action_type:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=action_type,
                    code="INVALID_REQUEST",
                    message="gameId와 type은 필수입니다.",
                    revision=-1,
                ),
                to=sid,
            )
            return

        client_revision = await parse_known_revision(
            sid=sid,
            data=data,
            game_id=game_id,
        )
        if "knownRevision" in data and client_revision is None:
            return

        try:
            result = await action_service.execute_action(
                game_id=str(game_id),
                user_id=user_id,
                action_type=action_type,
                data=data,
                known_revision=client_revision,
            )
            state = result.state
            events = result.events
            patches = result.patches
            previous_turn = result.previous_turn
            previous_player_id = result.previous_player_id
            await sio.enter_room(sid, f"game:{game_id}")
            await sync_runtime.set_active_game(
                user_id=user_id,
                game_id=str(game_id),
            )

        except LockAcquisitionError:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=action_type,
                    code="RETRY_LATER",
                    message="잠시 후 다시 시도해주세요.",
                    revision=-1,
                ),
                to=sid,
            )
            return
        except GameNotFoundError:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=action_type,
                    code="GAME_NOT_FOUND",
                    message="게임을 찾을 수 없습니다.",
                    revision=-1,
                ),
                to=sid,
            )
            return
        except GameDesyncError:
            await emit_desync_error(
                sid=sid,
                game_id=game_id,
                message="클라이언트 상태가 서버보다 오래되었습니다.",
            )
            return
        except GameMembershipError:
            await emit_game_error(
                sid=sid,
                game_id=str(game_id),
                code="NOT_GAME_MEMBER",
                message="게임 참가자가 아닙니다.",
            )
            return
        except GameActionError as exc:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=action_type,
                    code=exc.code,
                    message=exc.message,
                    revision=state.revision if state else -1,
                ),
                to=sid,
            )
            return

        packet = await sync_runtime.build_and_store_patch_packet(
            state=state,
            events=events,
            patches=patches,
            include_snapshot=False,
        )

        if state.status == "playing" and did_turn_advance(
            state,
            previous_turn=previous_turn,
            previous_player_id=previous_player_id,
        ):
            start_turn_timer(game_id, sio)

        await sio.emit(
            "game:ack",
            {
                "gameId": game_id,
                "actionId": action_id,
                "type": action_type,
                "ok": True,
                "error": None,
                "revision": state.revision,
            },
            to=sid,
        )
        await sio.emit("game:patch", packet, room=f"game:{game_id}")
        await emit_prompt_if_needed(state)
        if state.status == "finished":
            await sync_runtime.finalize_finished_game(state)

    @sio.on("game:prompt_response")
    async def handle_prompt_response(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        if user_id is None:
            await emit_game_error(
                sid=sid, code="AUTH_REQUIRED", message="인증이 필요합니다."
            )
            return

        game_id = data.get("gameId")
        prompt_id = str(data.get("promptId") or "")
        choice = str(data.get("choice") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else None
        action_id = f"prompt:{prompt_id}" if prompt_id else "prompt:unknown"
        state = None
        events: list[dict] = []
        patches: list[dict] = []

        if not game_id or not prompt_id or not choice:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=PROMPT_RESPONSE_ACK_TYPE,
                    code="INVALID_REQUEST",
                    message="gameId, promptId, choice는 필수입니다.",
                    revision=-1,
                    prompt_id=prompt_id or None,
                ),
                to=sid,
            )
            return

        client_revision = await parse_known_revision(
            sid=sid,
            data=data,
            game_id=game_id,
        )
        if "knownRevision" in data and client_revision is None:
            return

        try:
            result = await action_service.respond_prompt(
                game_id=str(game_id),
                user_id=user_id,
                prompt_id=prompt_id,
                choice=choice,
                payload=payload,
                known_revision=client_revision,
            )
            state = result.state
            events = result.events
            patches = result.patches
            previous_turn = result.previous_turn
            previous_player_id = result.previous_player_id
            await sio.enter_room(sid, f"game:{game_id}")
            await sync_runtime.set_active_game(
                user_id=user_id,
                game_id=str(game_id),
            )

        except LockAcquisitionError:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=PROMPT_RESPONSE_ACK_TYPE,
                    code="RETRY_LATER",
                    message="잠시 후 다시 시도해주세요.",
                    revision=-1,
                    prompt_id=prompt_id,
                ),
                to=sid,
            )
            return
        except GameNotFoundError:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=PROMPT_RESPONSE_ACK_TYPE,
                    code="GAME_NOT_FOUND",
                    message="게임을 찾을 수 없습니다.",
                    revision=-1,
                    prompt_id=prompt_id,
                ),
                to=sid,
            )
            return
        except GameDesyncError:
            await emit_desync_error(
                sid=sid,
                game_id=game_id,
                message="????????????? ?????? ???????????",
            )
            return
        except GameMembershipError:
            await emit_game_error(
                sid=sid,
                game_id=str(game_id),
                code="NOT_GAME_MEMBER",
                message="??? ?????? ??????.",
            )
            return
        except GameActionError as exc:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=PROMPT_RESPONSE_ACK_TYPE,
                    code=exc.code,
                    message=exc.message,
                    revision=state.revision if state else -1,
                    prompt_id=prompt_id,
                ),
                to=sid,
            )
            return

        packet = await sync_runtime.build_and_store_patch_packet(
            state=state,
            events=events,
            patches=patches,
            include_snapshot=False,
        )

        if state.status == "playing" and did_turn_advance(
            state,
            previous_turn=previous_turn,
            previous_player_id=previous_player_id,
        ):
            start_turn_timer(game_id, sio)

        await sio.emit(
            "game:ack",
            {
                "gameId": game_id,
                "actionId": action_id,
                "type": PROMPT_RESPONSE_ACK_TYPE,
                "ok": True,
                "error": None,
                "revision": state.revision,
                "promptId": prompt_id,
            },
            to=sid,
        )
        await sio.emit("game:patch", packet, room=f"game:{game_id}")
        await emit_prompt_if_needed(state)
        if state.status == "finished":
            await sync_runtime.finalize_finished_game(state)
