from __future__ import annotations

import socketio

from app.game.actions.end_turn import process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.enums import ActionType, ServerEventType
from app.game.errors import GameActionError
from app.game.presentation import serialize_game_patch
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
from app.game.timer import start_turn_timer
from app.services.room_service import RoomService

PROMPT_RESPONSE_ACK_TYPE = "PROMPT_RESPONSE"


def register_game_handlers(
    sio: socketio.AsyncServer,
    sid_to_user: dict[str, int],
) -> None:
    room_service = RoomService()

    async def emit_prompt_if_needed(state: dict) -> None:
        prompt_payload = serialize_prompt(state.get("pending_prompt"))
        if not prompt_payload:
            return

        prompt_player_id = state["pending_prompt"]["player_id"]
        await sio.emit("game:prompt", prompt_payload, room=f"user:{prompt_player_id}")

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
            "error": {
                "code": code,
                "message": message,
            },
            "revision": revision,
            "promptId": prompt_id,
        }

    @sio.on("enter_room")
    async def enter_room(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        room_id = str(data.get("room_id") or "")

        if user_id is None:
            await sio.emit("game:error", {"code": "AUTH_REQUIRED", "message": "Authentication required."}, to=sid)
            return

        room = await room_service.get_room(room_id)
        if room is None:
            await sio.emit("game:error", {"code": "ROOM_NOT_FOUND", "message": "Room not found."}, to=sid)
            return

        if not any(player["id"] == str(user_id) for player in room["players"]):
            await sio.emit("game:error", {"code": "NOT_ROOM_MEMBER", "message": "You are not in this room."}, to=sid)
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
            await sio.emit("game:error", {"code": "AUTH_REQUIRED", "message": "Authentication required."}, to=sid)
            return

        if not room_id or not message:
            return

        _room, chat_message = await room_service.add_chat_message(
            room_id=room_id,
            user_id=user_id,
            message=message,
        )
        await sio.emit("chat", {"room_id": room_id, **chat_message}, room=f"room:{room_id}")

    @sio.on("game:sync")
    async def game_sync(sid: str, data: dict) -> None:
        game_id = data.get("gameId")
        known_revision = data.get("knownRevision", -1)

        if not game_id:
            await sio.emit(
                "game:error",
                {
                    "gameId": None,
                    "code": "INVALID_REQUEST",
                    "message": "gameId is required.",
                },
                to=sid,
            )
            return

        state = await get_game_state(game_id)
        if state is None:
            await sio.emit(
                "game:error",
                {
                    "gameId": game_id,
                    "code": "GAME_NOT_FOUND",
                    "message": "Game not found.",
                },
                to=sid,
            )
            return

        await sio.enter_room(sid, f"game:{game_id}")
        await sio.emit(
            "game:patch",
            serialize_game_patch(
                state,
                events=[
                    {
                        "type": ServerEventType.SYNCED,
                        "knownRevision": known_revision,
                        "currentRevision": state["revision"],
                    }
                ],
            ),
            to=sid,
        )
        await emit_prompt_if_needed(state)

    @sio.on("game:action")
    async def handle_game_action(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        if user_id is None:
            await sio.emit("game:error", {"code": "AUTH_REQUIRED", "message": "Authentication required."}, to=sid)
            return

        game_id = data.get("gameId")
        action_id = data.get("actionId", "")
        action_type = data.get("type")
        state = None

        if not game_id or not action_type:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=action_type,
                    code="INVALID_REQUEST",
                    message="gameId and type are required.",
                    revision=-1,
                ),
                to=sid,
            )
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
                            message="Game not found.",
                            revision=-1,
                        ),
                        to=sid,
                    )
                    return

                if action_type == ActionType.ROLL_DICE:
                    events, patches = process_roll_dice(state, user_id)
                    apply_patches(state, patches)
                    if state.get("pending_prompt") is None and state["status"] == "playing":
                        end_events, end_patches = process_end_turn(state, user_id)
                        apply_patches(state, end_patches)
                        events.extend(end_events)
                elif action_type == ActionType.BUY_PROPERTY:
                    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
                    tile_id = int(payload.get("tileId", -1))
                    events, patches = process_buy_property_action(
                        state,
                        player_id=user_id,
                        tile_id=tile_id,
                    )
                    apply_patches(state, patches)
                    if state.get("pending_prompt") is None and state["status"] == "playing":
                        end_events, end_patches = process_end_turn(state, user_id)
                        apply_patches(state, end_patches)
                        events.extend(end_events)
                elif action_type == ActionType.SELL_PROPERTY:
                    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
                    tile_id = int(payload.get("tileId", -1))
                    raw_building_level = payload.get("buildingLevel")
                    building_level = (
                        int(raw_building_level)
                        if isinstance(raw_building_level, (int, str)) and str(raw_building_level).strip() != ""
                        else None
                    )
                    events, patches = process_sell_property_action(
                        state,
                        player_id=user_id,
                        tile_id=tile_id,
                        building_level=building_level,
                    )
                    apply_patches(state, patches)
                    if state.get("pending_prompt") is None and state["status"] == "playing":
                        end_events, end_patches = process_end_turn(state, user_id)
                        apply_patches(state, end_patches)
                        events.extend(end_events)
                elif action_type == ActionType.END_TURN:
                    events, patches = process_end_turn(state, user_id)
                    apply_patches(state, patches)
                else:
                    await sio.emit(
                        "game:ack",
                        build_error_ack(
                            game_id=game_id,
                            action_id=action_id,
                            action_type=action_type,
                            code="UNKNOWN_ACTION",
                            message=f"Unsupported action: {action_type}",
                            revision=state["revision"],
                        ),
                        to=sid,
                    )
                    return

                state["revision"] += 1
                await save_game_state(game_id, state)

        except LockAcquisitionError:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=action_type,
                    code="RETRY_LATER",
                    message="Try again shortly.",
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
                    revision=state["revision"] if state else -1,
                ),
                to=sid,
            )
            return

        if state["status"] == "playing":
            start_turn_timer(game_id, sio)

        await sio.emit(
            "game:ack",
            {
                "gameId": game_id,
                "actionId": action_id,
                "type": action_type,
                "ok": True,
                "error": None,
                "revision": state["revision"],
            },
            to=sid,
        )
        await sio.emit(
            "game:patch",
            serialize_game_patch(state, events=events),
            room=f"game:{game_id}",
        )
        await emit_prompt_if_needed(state)

    @sio.on("game:prompt_response")
    async def handle_prompt_response(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        if user_id is None:
            await sio.emit("game:error", {"code": "AUTH_REQUIRED", "message": "Authentication required."}, to=sid)
            return

        game_id = data.get("gameId")
        prompt_id = str(data.get("promptId") or "")
        choice = str(data.get("choice") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else None
        action_id = f"prompt:{prompt_id}" if prompt_id else "prompt:unknown"
        state = None

        if not game_id or not prompt_id or not choice:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=PROMPT_RESPONSE_ACK_TYPE,
                    code="INVALID_REQUEST",
                    message="gameId, promptId, and choice are required.",
                    revision=-1,
                    prompt_id=prompt_id or None,
                ),
                to=sid,
            )
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
                            message="Game not found.",
                            revision=-1,
                            prompt_id=prompt_id,
                        ),
                        to=sid,
                    )
                    return

                events, patches = process_prompt_response(
                    state,
                    player_id=user_id,
                    prompt_id=prompt_id,
                    choice=choice,
                    payload=payload,
                )
                apply_patches(state, patches)

                if state["status"] == "playing" and state.get("pending_prompt") is None:
                    end_events, end_patches = process_end_turn(state, user_id)
                    apply_patches(state, end_patches)
                    events.extend(end_events)

                state["revision"] += 1
                await save_game_state(game_id, state)

        except LockAcquisitionError:
            await sio.emit(
                "game:ack",
                build_error_ack(
                    game_id=game_id,
                    action_id=action_id,
                    action_type=PROMPT_RESPONSE_ACK_TYPE,
                    code="RETRY_LATER",
                    message="Try again shortly.",
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
                    revision=state["revision"] if state else -1,
                    prompt_id=prompt_id,
                ),
                to=sid,
            )
            return

        if state["status"] == "playing":
            start_turn_timer(game_id, sio)

        await sio.emit(
            "game:ack",
            {
                "gameId": game_id,
                "actionId": action_id,
                "type": PROMPT_RESPONSE_ACK_TYPE,
                "ok": True,
                "error": None,
                "revision": state["revision"],
                "promptId": prompt_id,
            },
            to=sid,
        )
        await sio.emit(
            "game:patch",
            serialize_game_patch(state, events=events),
            room=f"game:{game_id}",
        )
        await emit_prompt_if_needed(state)

