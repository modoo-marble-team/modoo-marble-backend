from __future__ import annotations

import random

from app.game.board import BOARD_SIZE, ISLAND_TILE_ID, START_SALARY, TILE_MAP
from app.game.enums import PlayerState, ServerEventType
from app.game.errors import GameActionError
from app.game.models import GameState
from app.game.patch import op_inc, op_set
from app.game.rules import PHASE_WAIT_ROLL, resolve_landing
from app.game.state import apply_patches


def _roll() -> tuple[int, int]:
    return random.randint(1, 6), random.randint(1, 6)


def _preview_state(state: GameState) -> GameState:
    return state.clone()


def _add_movement(
    player_id: int,
    from_tile: int,
    to_tile: int,
    events: list[dict],
    patches: list[dict],
) -> None:
    patches.append(op_set(f"players.{player_id}.current_tile_id", to_tile))
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
    if tile_def is None:
        return

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
    player = state.player(player_id)
    if player is None:
        raise GameActionError(code="PLAYER_NOT_FOUND", message="플레이어를 찾을 수 없습니다.")
    if state.current_player_id != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="내 턴이 아닙니다.")
    if state.status != "playing":
        raise GameActionError(code="INVALID_PHASE", message="진행 중인 게임이 아닙니다.")
    if state.phase != PHASE_WAIT_ROLL:
        raise GameActionError(
            code="INVALID_PHASE",
            message="주사위는 턴 시작 시에만 굴릴 수 있습니다.",
        )
    if player.player_state == PlayerState.BANKRUPT:
        raise GameActionError(
            code="PLAYER_BANKRUPT",
            message="파산한 플레이어는 행동할 수 없습니다.",
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

    if player.player_state == PlayerState.LOCKED:
        patches.append(op_set("phase", "RESOLVING"))
        if is_double:
            patches.extend(
                [
                    op_set(f"players.{player_id}.player_state", PlayerState.NORMAL),
                    op_set(f"players.{player_id}.state_duration", 0),
                    op_set(f"players.{player_id}.consecutive_doubles", 0),
                ]
            )
            events.append(
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "playerId": player_id,
                    "playerState": PlayerState.NORMAL,
                    "reason": "double_escape",
                }
            )
            from_tile = player.current_tile_id
            to_tile = (from_tile + total) % BOARD_SIZE
            _add_movement(player_id, from_tile, to_tile, events, patches)
            preview_state = _preview_state(state)
            apply_patches(preview_state, patches)
            landing_events, landing_patches = resolve_landing(
                preview_state,
                player_id,
                to_tile,
            )
            events.extend(landing_events)
            patches.extend(landing_patches)
            return events, patches

        new_duration = player.state_duration - 1
        if new_duration <= 0:
            patches.extend(
                [
                    op_set(f"players.{player_id}.player_state", PlayerState.NORMAL),
                    op_set(f"players.{player_id}.state_duration", 0),
                ]
            )
            events.append(
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "playerId": player_id,
                    "playerState": PlayerState.NORMAL,
                    "reason": "timeout_escape",
                }
            )
        else:
            patches.append(op_set(f"players.{player_id}.state_duration", new_duration))
        return events, patches

    new_consecutive = player.consecutive_doubles + 1 if is_double else 0

    if is_double and new_consecutive >= 3:
        from_tile = player.current_tile_id
        patches.extend(
            [
                op_set(f"players.{player_id}.current_tile_id", ISLAND_TILE_ID),
                op_set(f"players.{player_id}.player_state", PlayerState.LOCKED),
                op_set(f"players.{player_id}.state_duration", 3),
                op_set(f"players.{player_id}.consecutive_doubles", 0),
            ]
        )
        events.extend(
            [
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
        )
        return events, patches

    patches.append(op_set(f"players.{player_id}.consecutive_doubles", new_consecutive))

    from_tile = player.current_tile_id
    to_tile = (from_tile + total) % BOARD_SIZE
    if from_tile + total >= BOARD_SIZE:
        patches.append(op_inc(f"players.{player_id}.balance", START_SALARY))
        events.append(
            {
                "type": "PASSED_START",
                "playerId": player_id,
                "salary": START_SALARY,
            }
        )

    _add_movement(player_id, from_tile, to_tile, events, patches)
    preview_state = _preview_state(state)
    apply_patches(preview_state, patches)
    landing_events, landing_patches = resolve_landing(preview_state, player_id, to_tile)
    events.extend(landing_events)
    patches.extend(landing_patches)
    return events, patches
