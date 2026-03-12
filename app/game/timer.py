from __future__ import annotations

import asyncio

import socketio

TURN_TIMEOUT_SECONDS = 30
_timers: dict[str, asyncio.Task] = {}


async def _auto_end_turn(game_id: str, sio: socketio.AsyncServer) -> None:
    await asyncio.sleep(TURN_TIMEOUT_SECONDS)

    from app.game.actions.end_turn import process_end_turn
    from app.game.errors import GameActionError
    from app.game.presentation import serialize_game_patch
    from app.game.rules import default_prompt_choice, process_prompt_response
    from app.game.state import (
        LockAcquisitionError,
        apply_patches,
        game_lock,
        get_game_state,
        save_game_state,
    )

    try:
        async with game_lock(game_id):
            state = await get_game_state(game_id)
            if state is None or state["status"] != "playing":
                return

            player_id = state["current_player_id"]
            if state.get("pending_prompt") is not None:
                prompt = state["pending_prompt"]
                events, patches = process_prompt_response(
                    state,
                    player_id=player_id,
                    prompt_id=prompt["prompt_id"],
                    choice=default_prompt_choice(prompt),
                )
                apply_patches(state, patches)
                end_events, end_patches = process_end_turn(state, player_id)
                apply_patches(state, end_patches)
                events.extend(end_events)
            else:
                events, patches = process_end_turn(state, player_id)
                apply_patches(state, patches)

            state["revision"] += 1
            await save_game_state(game_id, state)

        await sio.emit(
            "game:patch",
            serialize_game_patch(state, events=events),
            room=f"game:{game_id}",
        )

        if state["status"] == "playing":
            start_turn_timer(game_id, sio)

    except (LockAcquisitionError, GameActionError):
        pass


def start_turn_timer(game_id: str, sio: socketio.AsyncServer) -> None:
    cancel_turn_timer(game_id)
    task = asyncio.create_task(_auto_end_turn(game_id, sio))
    _timers[game_id] = task



def cancel_turn_timer(game_id: str) -> None:
    task = _timers.pop(game_id, None)
    if task and not task.done():
        task.cancel()

