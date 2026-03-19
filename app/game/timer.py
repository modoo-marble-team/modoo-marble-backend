from __future__ import annotations

import asyncio

import socketio

TURN_TIMEOUT_SECONDS = 30
_timers: dict[str, asyncio.Task] = {}


async def _auto_end_turn(game_id: str, sio: socketio.AsyncServer) -> None:
    await asyncio.sleep(TURN_TIMEOUT_SECONDS)

    from app.game.actions.end_turn import process_end_turn
    from app.game.errors import GameActionError
    from app.game.rules import default_prompt_choice, process_prompt_response
    from app.game.state import (
        LockAcquisitionError,
        apply_patches,
        game_lock,
        get_game_state,
        save_game_state,
    )
    from app.game.sync_runtime import init_game_sync_runtime

    try:
        async with game_lock(game_id):
            state = await get_game_state(game_id)
            if state is None or state.status != "playing":
                return

            player_id = state.current_player_id
            all_patches: list[dict] = []
            if state.pending_prompt is not None:
                prompt = state.pending_prompt
                events, patches = process_prompt_response(
                    state,
                    player_id=player_id,
                    prompt_id=prompt.prompt_id,
                    choice=default_prompt_choice(prompt),
                )
                apply_patches(state, patches)
                all_patches.extend(patches)
                end_events, end_patches = process_end_turn(state, player_id)
                apply_patches(state, end_patches)
                events.extend(end_events)
                all_patches.extend(end_patches)
            else:
                events, patches = process_end_turn(state, player_id)
                apply_patches(state, patches)
                all_patches.extend(patches)

            state.revision += 1
            await save_game_state(game_id, state)

        runtime = init_game_sync_runtime(sio)
        packet = await runtime.build_and_store_patch_packet(
            state=state,
            events=events,
            patches=all_patches,
            include_snapshot=False,
        )
        await sio.emit("game:patch", packet, room=f"game:{game_id}")

        if state.status == "playing":
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
