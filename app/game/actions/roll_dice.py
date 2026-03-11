from __future__ import annotations

import random

from app.game.board import BOARD_SIZE, ISLAND_TILE_ID, START_SALARY, TILE_MAP
from app.game.enums import PlayerState, ServerEventType
from app.game.schemas import GameState


def _roll() -> tuple[int, int]:
    """주사위 2개 굴리기. 각각 1~6."""
    return random.randint(1, 6), random.randint(1, 6)


def _add_movement(
    player_id: int,
    from_tile: int,
    to_tile: int,
    events: list,
    patches: list,
) -> None:
    """이동 패치와 착지 이벤트를 목록에 추가한다. (in-place 수정)"""
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
    """
    주사위 굴리기 전체 처리.
    반환값: (events, patches)
    실패 시 ValueError 발생.
    """
    # ── 유효성 검사 ─────────────────────────────────────
    player = state["players"].get(str(player_id))
    if player is None:
        raise ValueError("플레이어를 찾을 수 없습니다.")
    if state["current_player_id"] != player_id:
        raise ValueError("지금 당신의 턴이 아닙니다.")
    if state["status"] != "playing":
        raise ValueError("게임이 진행 중이 아닙니다.")
    if player["state"] == PlayerState.BANKRUPT:
        raise ValueError("파산한 플레이어는 행동할 수 없습니다.")

    dice1, dice2 = _roll()
    total = dice1 + dice2
    is_double = dice1 == dice2

    events: list[dict] = []
    patches: list[dict] = []

    # 주사위 결과 이벤트 (항상 첫 번째로 기록)
    events.append(
        {
            "type": ServerEventType.DICE_ROLLED,
            "player_id": player_id,
            "dice": [dice1, dice2],
            "is_double": is_double,
        }
    )

    # ── 케이스 1: 무인도에 갇힌 상태 ───────────────────
    if player["state"] == PlayerState.LOCKED:
        if is_double:
            # 더블 → 탈출 후 이동
            patches += [
                {
                    "op": "set",
                    "path": f"players.{player_id}.state",
                    "value": PlayerState.NORMAL,
                },
                {
                    "op": "set",
                    "path": f"players.{player_id}.state_duration",
                    "value": 0,
                },
                {
                    "op": "set",
                    "path": f"players.{player_id}.consecutive_doubles",
                    "value": 0,
                },
            ]
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
        else:
            # 더블 아님 → 무인도 잔여 턴 차감
            new_duration = player["state_duration"] - 1
            if new_duration <= 0:
                # 3턴 경과 → 자동 탈출 (이동 없음)
                patches += [
                    {
                        "op": "set",
                        "path": f"players.{player_id}.state",
                        "value": PlayerState.NORMAL,
                    },
                    {
                        "op": "set",
                        "path": f"players.{player_id}.state_duration",
                        "value": 0,
                    },
                ]
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
        return events, patches  # 무인도 케이스는 여기서 종료

    # ── 케이스 2: 3연속 더블 → 무인도 ──────────────────
    new_consecutive = player["consecutive_doubles"] + 1 if is_double else 0

    if is_double and new_consecutive >= 3:
        from_tile = player["current_tile_id"]
        patches += [
            {
                "op": "set",
                "path": f"players.{player_id}.current_tile_id",
                "value": ISLAND_TILE_ID,
            },
            {
                "op": "set",
                "path": f"players.{player_id}.state",
                "value": PlayerState.LOCKED,
            },
            {"op": "set", "path": f"players.{player_id}.state_duration", "value": 3},
            {
                "op": "set",
                "path": f"players.{player_id}.consecutive_doubles",
                "value": 0,
            },
        ]
        events += [
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
        return events, patches  # 무인도 이동 후 종료

    # ── 케이스 3: 일반 이동 ─────────────────────────────
    patches.append(
        {
            "op": "set",
            "path": f"players.{player_id}.consecutive_doubles",
            "value": new_consecutive,
        }
    )

    from_tile = player["current_tile_id"]
    to_tile = (from_tile + total) % BOARD_SIZE

    # 출발점 통과 시 급여 지급
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
    return events, patches
