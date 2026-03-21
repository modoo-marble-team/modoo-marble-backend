from __future__ import annotations

import asyncio
import math
import random
import time

import socketio
from app.game.game_rules import TURN_TIMEOUT_SECONDS
_timers: dict[str, asyncio.Task] = {}
_turn_deadlines_ms: dict[str, int] = {}
_prompt_deadlines_ms: dict[str, tuple[str, int]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _remaining_ms(deadline_at_ms: int | None) -> int | None:
    if deadline_at_ms is None:
        return None
    return max(deadline_at_ms - _now_ms(), 0)


def _remaining_sec(remaining_ms: int | None) -> int | None:
    if remaining_ms is None:
        return None
    return max(math.ceil(remaining_ms / 1000), 0)


def sync_prompt_timer(*, game_id: str, prompt) -> int | None:
    if prompt is None:
        _prompt_deadlines_ms.pop(game_id, None)
        return None

    current = _prompt_deadlines_ms.get(game_id)
    if current is not None and current[0] == prompt.prompt_id:
        return current[1]

    deadline_at_ms = _now_ms() + max(int(prompt.timeout_sec), 0) * 1000
    _prompt_deadlines_ms[game_id] = (prompt.prompt_id, deadline_at_ms)
    return deadline_at_ms


def build_timer_sync_payload(*, game_id: str, state, user_id: int) -> dict:
    turn_deadline_at_ms = _turn_deadlines_ms.get(game_id)
    turn_remaining_ms = _remaining_ms(turn_deadline_at_ms)

    prompt_payload = None
    pending_prompt = state.pending_prompt
    if pending_prompt is not None and pending_prompt.player_id == user_id:
        prompt_deadline_at_ms = sync_prompt_timer(
            game_id=game_id,
            prompt=pending_prompt,
        )
        prompt_remaining_ms = _remaining_ms(prompt_deadline_at_ms)
        prompt_payload = {
            "promptId": pending_prompt.prompt_id,
            "type": pending_prompt.type,
            "timeoutSec": pending_prompt.timeout_sec,
            "deadlineAtMs": prompt_deadline_at_ms,
            "remainingMs": prompt_remaining_ms,
            "remainingSec": _remaining_sec(prompt_remaining_ms),
        }

    return {
        "gameId": game_id,
        "revision": state.revision,
        "serverTimeMs": _now_ms(),
        "turnDeadlineAtMs": turn_deadline_at_ms,
        "turnRemainingMs": turn_remaining_ms,
        "turnRemainingSec": _remaining_sec(turn_remaining_ms),
        "prompt": prompt_payload,
    }


def _resolve_afk_prompt_response(state, prompt) -> tuple[str, dict | None]:
    from app.game.board import BOARD_SIZE
    from app.game.rules import normalize_prompt_choice

    normalized_choices = [
        normalize_prompt_choice(choice.value) for choice in prompt.choices
    ]

    if prompt.type == "TRAVEL_SELECT":
        current_tile_id = state.require_player(prompt.player_id).current_tile_id
        candidates = [
            tile_id for tile_id in range(BOARD_SIZE) if tile_id != current_tile_id
        ]
        if candidates:
            return "CONFIRM", {"targetTileId": random.choice(candidates)}

    if len(normalized_choices) == 1:
        return normalized_choices[0], None

    if "SKIP" in normalized_choices:
        return "SKIP", None

    return random.choice(normalized_choices), None


def process_turn_timeout(state) -> tuple[list[dict], list[dict]]:
    from app.game.actions.end_turn import process_end_turn
    from app.game.actions.roll_dice import process_roll_dice
    from app.game.rules import PHASE_WAIT_ROLL, process_prompt_response
    from app.game.state import apply_patches

    player_id = state.current_player_id
    events: list[dict] = []
    patches: list[dict] = []

    if state.pending_prompt is None and state.phase == PHASE_WAIT_ROLL:
        roll_events, roll_patches = process_roll_dice(state, player_id)
        apply_patches(state, roll_patches)
        events.extend(roll_events)
        patches.extend(roll_patches)

    while state.status == "playing" and state.pending_prompt is not None:
        prompt = state.pending_prompt
        choice, payload = _resolve_afk_prompt_response(state, prompt)
        prompt_events, prompt_patches = process_prompt_response(
            state,
            player_id=player_id,
            prompt_id=prompt.prompt_id,
            choice=choice,
            payload=payload,
        )
        apply_patches(state, prompt_patches)
        events.extend(prompt_events)
        patches.extend(prompt_patches)

    if state.status == "playing" and state.pending_prompt is None:
        end_events, end_patches = process_end_turn(state, player_id)
        apply_patches(state, end_patches)
        events.extend(end_events)
        patches.extend(end_patches)

    return events, patches


async def _auto_end_turn(game_id: str, sio: socketio.AsyncServer) -> None:
    await asyncio.sleep(TURN_TIMEOUT_SECONDS)

    from app.game.errors import GameActionError
    from app.game.state import (
        LockAcquisitionError,
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

            events, all_patches = process_turn_timeout(state)
            sync_prompt_timer(game_id=game_id, prompt=state.pending_prompt)

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
    _turn_deadlines_ms[game_id] = _now_ms() + TURN_TIMEOUT_SECONDS * 1000
    task = asyncio.create_task(_auto_end_turn(game_id, sio))
    _timers[game_id] = task


def cancel_turn_timer(game_id: str) -> None:
    _turn_deadlines_ms.pop(game_id, None)
    _prompt_deadlines_ms.pop(game_id, None)
    task = _timers.pop(game_id, None)
    if task and not task.done():
        task.cancel()
