from __future__ import annotations

import random
from copy import deepcopy

from app.game.board import BOARD_SIZE, ISLAND_TILE_ID, START_SALARY, TILE_MAP
from app.game.enums import PlayerState, ServerEventType
from app.game.errors import GameActionError
from app.game.rules import PHASE_WAIT_ROLL, resolve_landing
from app.game.schemas import GameState
from app.game.state import apply_patches


def _roll() -> tuple[int, int]:
    return random.randint(1, 6), random.randint(1, 6)


def _add_movement(
    player_id: int,
    from_tile: int,
    to_tile: int,
    events: list,
    patches: list,
) -> None:
    patches.append(
        {"op": "set", "path": f"players.{player_id}.current_tile_id", "value": to_tile}
    )
    events.append(
        {
            "type": ServerEventType.PLAYER_MOVED,
            "player_id": player_id,
            "from_tile_id": from_tile,
            "to_tile_id": to_tile,
            "trigger": "normal",
        }
    )

    tile_def = TILE_MAP.get(to_tile)
    if tile_def:
        events.append(
            {
                "type": ServerEventType.LANDED,
                "player_id": player_id,
                "tile": {
                    "tile_id": tile_def.tile_id,
                    "name": tile_def.name,
                    "tile_type": tile_def.tile_type,
                    "tier": tile_def.tier,
                    "price": tile_def.price,
                },
            }
        )


def process_roll_dice(
    state: GameState,
    player_id: int,
) -> tuple[list[dict], list[dict]]:
    player = state["players"].get(str(player_id))
    if player is None:
        raise GameActionError(code="PLAYER_NOT_FOUND", message="Player not found.")
    if state["current_player_id"] != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="It is not your turn.")
    if state["status"] != "playing":
        raise GameActionError(code="INVALID_PHASE", message="Game is not active.")
    if state["phase"] != PHASE_WAIT_ROLL:
        raise GameActionError(code="INVALID_PHASE", message="Dice can only be rolled at turn start.")
    if player["state"] == PlayerState.BANKRUPT:
        raise GameActionError(code="PLAYER_BANKRUPT", message="Bankrupt players cannot act.")

    dice1, dice2 = _roll()
    total = dice1 + dice2
    is_double = dice1 == dice2

    events: list[dict] = [
        {
            "type": ServerEventType.DICE_ROLLED,
            "player_id": player_id,
            "dice": [dice1, dice2],
            "is_double": is_double,
        }
    ]
    patches: list[dict] = []

    if player["state"] == PlayerState.LOCKED:
        patches.append({"op": "set", "path": "phase", "value": "RESOLVING"})
        if is_double:
            patches.extend(
                [
                    {"op": "set", "path": f"players.{player_id}.state", "value": PlayerState.NORMAL},
                    {"op": "set", "path": f"players.{player_id}.state_duration", "value": 0},
                    {"op": "set", "path": f"players.{player_id}.consecutive_doubles", "value": 0},
                ]
            )
            events.append(
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "player_id": player_id,
                    "state": PlayerState.NORMAL,
                    "reason": "double_escape",
                }
            )
            from_tile = player["current_tile_id"]
            to_tile = (from_tile + total) % BOARD_SIZE
            _add_movement(player_id, from_tile, to_tile, events, patches)
            preview_state = deepcopy(state)
            apply_patches(preview_state, patches)
            landing_events, landing_patches = resolve_landing(preview_state, player_id, to_tile)
            events.extend(landing_events)
            patches.extend(landing_patches)
            return events, patches

        new_duration = player["state_duration"] - 1
        if new_duration <= 0:
            patches.extend(
                [
                    {"op": "set", "path": f"players.{player_id}.state", "value": PlayerState.NORMAL},
                    {"op": "set", "path": f"players.{player_id}.state_duration", "value": 0},
                ]
            )
            events.append(
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "player_id": player_id,
                    "state": PlayerState.NORMAL,
                    "reason": "timeout_escape",
                }
            )
        else:
            patches.append(
                {
                    "op": "set",
                    "path": f"players.{player_id}.state_duration",
                    "value": new_duration,
                }
            )
        return events, patches

    new_consecutive = player["consecutive_doubles"] + 1 if is_double else 0
    if is_double and new_consecutive >= 3:
        from_tile = player["current_tile_id"]
        patches.extend(
            [
                {"op": "set", "path": f"players.{player_id}.current_tile_id", "value": ISLAND_TILE_ID},
                {"op": "set", "path": f"players.{player_id}.state", "value": PlayerState.LOCKED},
                {"op": "set", "path": f"players.{player_id}.state_duration", "value": 3},
                {"op": "set", "path": f"players.{player_id}.consecutive_doubles", "value": 0},
                {"op": "set", "path": "phase", "value": "RESOLVING"},
            ]
        )
        events.extend(
            [
                {
                    "type": ServerEventType.PLAYER_MOVED,
                    "player_id": player_id,
                    "from_tile_id": from_tile,
                    "to_tile_id": ISLAND_TILE_ID,
                    "trigger": "triple_double",
                },
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "player_id": player_id,
                    "state": PlayerState.LOCKED,
                    "reason": "triple_double",
                },
            ]
        )
        return events, patches

    patches.append(
        {
            "op": "set",
            "path": f"players.{player_id}.consecutive_doubles",
            "value": new_consecutive,
        }
    )

    from_tile = player["current_tile_id"]
    to_tile = (from_tile + total) % BOARD_SIZE
    if from_tile + total >= BOARD_SIZE:
        patches.append(
            {"op": "inc", "path": f"players.{player_id}.balance", "value": START_SALARY}
        )
        events.append(
            {
                "type": "PASSED_START",
                "player_id": player_id,
                "salary": START_SALARY,
            }
        )

    _add_movement(player_id, from_tile, to_tile, events, patches)
    preview_state = deepcopy(state)
    apply_patches(preview_state, patches)
    landing_events, landing_patches = resolve_landing(preview_state, player_id, to_tile)
    events.extend(landing_events)
    patches.extend(landing_patches)
    return events, patches

