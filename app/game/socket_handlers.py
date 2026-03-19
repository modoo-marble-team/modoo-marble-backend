from __future__ import annotations

import socketio
import structlog

from app.game.actions.end_turn import process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.enums import ActionType
from app.game.errors import GameActionError
from app.game.models import GameState
from app.game.rules import (
    process_buy_property_action,
    process_prompt_response,
    process_sell_property_action,
    serialize_prompt,
)
from app.game.state import (
    LockAcquisitionError,
    apply_patches,
    game_lock,
    get_game_state,
    save_game_state,
)
from app.game.sync_runtime import init_game_sync_runtime
from app.game.timer import start_turn_timer
from app.services.room_service import RoomService

logger = structlog.get_logger()

PROMPT_RESPONSE_ACK_TYPE = "PROMPT_RESPONSE"


def register_game_handlers(
    sio: socketio.AsyncServer,
    sid_to_user: dict[str, int],
) -> None:
    room_service = RoomService()
    sync_runtime = init_game_sync_runtime(sio)

    async def emit_prompt_if_needed(state: GameState) -> None:
        prompt_payload = serialize_prompt(state.pending_prompt)
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
        if user_id not in state.players:
            await sio.emit(
                "game:error",
                {
                    "gameId": game_id,
                    "code": "NOT_GAME_MEMBER",
                    "message": "게임 참가자가 아닙니다.",
                },
                to=sid,
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
        await sio.emit(
            "game:error",
            {
                "gameId": game_id,
                "code": "DESYNC",
                "message": message,
            },
            to=sid,
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

    def maybe_end_turn(
        state: GameState,
        user_id: int,
        events: list[dict],
        patches: list[dict],
    ) -> None:
        if state.pending_prompt is None and state.status == "playing":
            end_events, end_patches = process_end_turn(state, user_id)
            apply_patches(state, end_patches)
            events.extend(end_events)
            patches.extend(end_patches)

    def parse_travel_target(payload: dict) -> int:
        raw_target = payload.get("targetTileId")
        if raw_target is None:
            raw_target = payload.get("toTileId")
        if raw_target is None:
            raw_target = payload.get("toIndex")
        try:
            return int(raw_target)
        except (TypeError, ValueError) as exc:
            raise GameActionError(
                code="INVALID_TILE",
                message="여행 목적지를 선택해주세요.",
            ) from exc

    @sio.on("enter_room")
    async def enter_room(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        room_id = str(data.get("room_id") or "")

        if user_id is None:
            await sio.emit(
                "game:error",
                {"code": "AUTH_REQUIRED", "message": "인증이 필요합니다."},
                to=sid,
            )
            return

        room = await room_service.get_room(room_id)
        if room is None:
            await sio.emit(
                "game:error",
                {"code": "ROOM_NOT_FOUND", "message": "방을 찾을 수 없습니다."},
                to=sid,
            )
            return

        if not any(player["id"] == str(user_id) for player in room["players"]):
            await sio.emit(
                "game:error",
                {"code": "NOT_ROOM_MEMBER", "message": "방 멤버가 아닙니다."},
                to=sid,
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
            await sio.emit(
                "game:error",
                {"code": "AUTH_REQUIRED", "message": "인증이 필요합니다."},
                to=sid,
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
            await sio.emit(
                "game:error",
                {
                    "gameId": game_id or None,
                    "code": "AUTH_REQUIRED",
                    "message": "인증이 필요합니다.",
                },
                to=sid,
            )
            return

        if not game_id:
            await sio.emit(
                "game:error",
                {
                    "gameId": None,
                    "code": "INVALID_REQUEST",
                    "message": "gameId가 필요합니다.",
                },
                to=sid,
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

    @sio.on("game:action")
    async def handle_game_action(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        if user_id is None:
            await sio.emit(
                "game:error",
                {"code": "AUTH_REQUIRED", "message": "인증이 필요합니다."},
                to=sid,
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
            async with game_lock(game_id):
                state = await get_game_state(game_id)
                if state is None:
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

                if client_revision is not None and state.revision != client_revision:
                    await emit_desync_error(
                        sid=sid,
                        game_id=game_id,
                        message="클라이언트 상태가 서버보다 오래되었습니다.",
                    )
                    return

                if not await ensure_game_room_membership(
                    sid=sid,
                    game_id=str(game_id),
                    state=state,
                    user_id=user_id,
                ):
                    return

                if action_type == ActionType.ROLL_DICE:
                    events, patches = process_roll_dice(state, user_id)
                    apply_patches(state, patches)
                    maybe_end_turn(state, user_id, events, patches)

                elif action_type == ActionType.BUY_PROPERTY:
                    payload = (
                        data.get("payload")
                        if isinstance(data.get("payload"), dict)
                        else {}
                    )
                    tile_id = int(payload.get("tileId", -1))
                    events, patches = process_buy_property_action(
                        state,
                        player_id=user_id,
                        tile_id=tile_id,
                    )
                    apply_patches(state, patches)
                    maybe_end_turn(state, user_id, events, patches)

                elif action_type == ActionType.SELL_PROPERTY:
                    payload = (
                        data.get("payload")
                        if isinstance(data.get("payload"), dict)
                        else {}
                    )
                    tile_id = int(payload.get("tileId", -1))
                    raw_building_level = payload.get("buildingLevel")
                    building_level = (
                        int(raw_building_level)
                        if isinstance(raw_building_level, (int, str))
                        and str(raw_building_level).strip() != ""
                        else None
                    )
                    events, patches = process_sell_property_action(
                        state,
                        player_id=user_id,
                        tile_id=tile_id,
                        building_level=building_level,
                    )
                    apply_patches(state, patches)
                    maybe_end_turn(state, user_id, events, patches)

                elif action_type == ActionType.END_TURN:
                    events, patches = process_end_turn(state, user_id)
                    apply_patches(state, patches)

                elif action_type == "TRAVEL":
                    payload = (
                        data.get("payload")
                        if isinstance(data.get("payload"), dict)
                        else {}
                    )
                    pending_prompt = state.pending_prompt
                    if pending_prompt is None or pending_prompt.type != "TRAVEL_SELECT":
                        raise GameActionError(
                            code="INVALID_PHASE",
                            message="여행지 선택 대기 상태가 아닙니다.",
                        )

                    target_tile_id = parse_travel_target(payload)
                    events, patches = process_prompt_response(
                        state,
                        player_id=user_id,
                        prompt_id=pending_prompt.prompt_id,
                        choice="CONFIRM",
                        payload={"targetTileId": target_tile_id},
                    )
                    apply_patches(state, patches)
                    maybe_end_turn(state, user_id, events, patches)

                else:
                    await sio.emit(
                        "game:ack",
                        build_error_ack(
                            game_id=game_id,
                            action_id=action_id,
                            action_type=action_type,
                            code="UNKNOWN_ACTION",
                            message=f"지원하지 않는 액션입니다: {action_type}",
                            revision=state.revision,
                        ),
                        to=sid,
                    )
                    return

                state.revision += 1
                await save_game_state(game_id, state)
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

        if state.status == "playing":
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
            await sio.emit(
                "game:error",
                {"code": "AUTH_REQUIRED", "message": "인증이 필요합니다."},
                to=sid,
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
            async with game_lock(game_id):
                state = await get_game_state(game_id)
                if state is None:
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

                if client_revision is not None and state.revision != client_revision:
                    await emit_desync_error(
                        sid=sid,
                        game_id=game_id,
                        message="클라이언트 상태가 서버보다 오래되었습니다.",
                    )
                    return

                if not await ensure_game_room_membership(
                    sid=sid,
                    game_id=str(game_id),
                    state=state,
                    user_id=user_id,
                ):
                    return

                events, patches = process_prompt_response(
                    state,
                    player_id=user_id,
                    prompt_id=prompt_id,
                    choice=choice,
                    payload=payload,
                )
                apply_patches(state, patches)
                maybe_end_turn(state, user_id, events, patches)

                state.revision += 1
                await save_game_state(game_id, state)
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

        if state.status == "playing":
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
