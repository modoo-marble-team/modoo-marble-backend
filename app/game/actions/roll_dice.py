from __future__ import annotations

import copy
import random

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
        {"op": "set", "path": f"players.{player_id}.currentTileId", "value": to_tile}
    )
    events.append(
        {
            "type": ServerEventType.PLAYER_MOVED,
            "playerId": player_id,
            "fromTileId": from_tile,
            "toTileId": to_tile,
            "trigger": "normal",
        }
    )

    tile_def = TILE_MAP.get(to_tile)
    if tile_def:
        events.append(
            {
                "type": ServerEventType.LANDED,
                "playerId": player_id,
                "tile": {
                    "tileId": tile_def.tile_id,
                    "name": tile_def.name,
                    "tileType": str(tile_def.tile_type),
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
        raise GameActionError(
            code="INVALID_PHASE", message="Dice can only be rolled at turn start."
        )
    if player["playerState"] == PlayerState.BANKRUPT:
        raise GameActionError(
            code="PLAYER_BANKRUPT", message="Bankrupt players cannot act."
        )

    dice1, dice2 = _roll()
    total = dice1 + dice2
    is_double = dice1 == dice2

    events: list[dict] = [
        {
            "type": ServerEventType.DICE_ROLLED,
            "playerId": player_id,
            "dice": [dice1, dice2],
            "isDouble": is_double,
        }
    ]
    patches: list[dict] = []

    # ── 케이스 1: 무인도에 갇힌 상태 ───────────────────
    if player["playerState"] == PlayerState.LOCKED:
        patches.append({"op": "set", "path": "phase", "value": "RESOLVING"})
        if is_double:
            # 더블 → 탈출 후 이동
            patches += [
                {
                    "op": "set",
                    "path": f"players.{player_id}.playerState",
                    "value": PlayerState.NORMAL,
                },
                {
                    "op": "set",
                    "path": f"players.{player_id}.stateDuration",
                    "value": 0,
                },
                {
                    "op": "set",
                    "path": f"players.{player_id}.consecutiveDoubles",
                    "value": 0,
                },
            ]
            events.append(
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "playerId": player_id,
                    "playerState": PlayerState.NORMAL,
                    "reason": "double_escape",
                }
            )
            from_tile = player["currentTileId"]
            to_tile = (from_tile + total) % BOARD_SIZE
            _add_movement(player_id, from_tile, to_tile, events, patches)
            preview_state = {
                **state,
                "players": copy.deepcopy(state["players"]),
                "tiles": copy.deepcopy(state["tiles"]),
            }
            apply_patches(preview_state, patches)
            landing_events, landing_patches = resolve_landing(
                preview_state, player_id, to_tile
            )
            events.extend(landing_events)
            patches.extend(landing_patches)
            return events, patches

        # 더블 아님 → 무인도 잔여 턴 차감
        new_duration = player["stateDuration"] - 1
        if new_duration <= 0:
            # 3턴 경과 → 자동 탈출 (이동 없음)
            patches += [
                {
                    "op": "set",
                    "path": f"players.{player_id}.playerState",
                    "value": PlayerState.NORMAL,
                },
                {
                    "op": "set",
                    "path": f"players.{player_id}.stateDuration",
                    "value": 0,
                },
            ]
            events.append(
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "playerId": player_id,
                    "playerState": PlayerState.NORMAL,
                    "reason": "timeout_escape",
                }
            )
        else:
            patches.append(
                {
                    "op": "set",
                    "path": f"players.{player_id}.stateDuration",
                    "value": new_duration,
                }
            )
        return events, patches  # 무인도 케이스는 여기서 종료

    # ── 케이스 2: 3연속 더블 → 무인도 ──────────────────
    new_consecutive = player["consecutiveDoubles"] + 1 if is_double else 0

    if is_double and new_consecutive >= 3:
        from_tile = player["currentTileId"]
        patches += [
            {
                "op": "set",
                "path": f"players.{player_id}.currentTileId",
                "value": ISLAND_TILE_ID,
            },
            {
                "op": "set",
                "path": f"players.{player_id}.playerState",
                "value": PlayerState.LOCKED,
            },
            {"op": "set", "path": f"players.{player_id}.stateDuration", "value": 3},
            {
                "op": "set",
                "path": f"players.{player_id}.consecutiveDoubles",
                "value": 0,
            },
        ]
        events += [
            {
                "type": ServerEventType.PLAYER_MOVED,
                "playerId": player_id,
                "fromTileId": from_tile,
                "toTileId": ISLAND_TILE_ID,
                "trigger": "triple_double",
            },
            {
                "type": ServerEventType.PLAYER_STATE_CHANGED,
                "playerId": player_id,
                "playerState": PlayerState.LOCKED,
                "reason": "triple_double",
            },
        ]
        return events, patches  # 무인도 이동 후 종료

    # ── 케이스 3: 일반 이동 ─────────────────────────────
    patches.append(
        {
            "op": "set",
            "path": f"players.{player_id}.consecutiveDoubles",
            "value": new_consecutive,
        }
    )

    from_tile = player["currentTileId"]
    to_tile = (from_tile + total) % BOARD_SIZE
    if from_tile + total >= BOARD_SIZE:
        patches.append(
            {"op": "inc", "path": f"players.{player_id}.balance", "value": START_SALARY}
        )
        events.append(
            {
                "type": "PASSED_START",
                "playerId": player_id,
                "salary": START_SALARY,
            }
        )

    _add_movement(player_id, from_tile, to_tile, events, patches)
    preview_state = {
        **state,
        "players": copy.deepcopy(state["players"]),
        "tiles": copy.deepcopy(state["tiles"]),
    }
    apply_patches(preview_state, patches)
    landing_events, landing_patches = resolve_landing(preview_state, player_id, to_tile)
    events.extend(landing_events)
    patches.extend(landing_patches)
    return events, patches
