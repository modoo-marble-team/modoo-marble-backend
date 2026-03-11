from __future__ import annotations

from app.game.enums import PlayerState, ServerEventType
from app.game.schemas import GameState


def get_next_player_id(state: GameState, current_player_id: int) -> int:
    """
    다음 턴을 할 플레이어 ID를 반환한다.
    파산한 플레이어는 건너뛴다.
    """
    # 파산하지 않은 플레이어만 turn_order 순으로 정렬
    active_players = sorted(
        [p for p in state["players"].values() if p["state"] != PlayerState.BANKRUPT],
        key=lambda p: p["turn_order"],
    )

    if not active_players:
        return current_player_id

    current_order = state["players"][str(current_player_id)]["turn_order"]

    # 현재보다 order가 높은 첫 번째 플레이어
    for p in active_players:
        if p["turn_order"] > current_order:
            return p["user_id"]

    # 없으면 처음으로 돌아감 (한 바퀴 완료)
    return active_players[0]["user_id"]


def process_end_turn(
    state: GameState,
    player_id: int,
) -> tuple[list[dict], list[dict]]:
    """
    턴 종료 처리.
    반환값: (events, patches)
    실패 시 ValueError 발생.
    """
    if state["current_player_id"] != player_id:
        raise ValueError("지금 당신의 턴이 아닙니다.")
    if state["status"] != "playing":
        raise ValueError("게임이 진행 중이 아닙니다.")

    events: list[dict] = []
    patches: list[dict] = []

    next_player_id = get_next_player_id(state, player_id)

    current_order = state["players"][str(player_id)]["turn_order"]
    next_order = state["players"][str(next_player_id)]["turn_order"]

    new_turn = state["turn"] + 1
    # 다음 플레이어의 order가 현재보다 작거나 같으면 한 바퀴 돈 것 → 라운드 증가
    new_round = state["round"] + 1 if next_order <= current_order else state["round"]

    patches += [
        {"op": "set", "path": "current_player_id", "value": next_player_id},
        {"op": "set", "path": "turn", "value": new_turn},
        {"op": "set", "path": "round", "value": new_round},
        # 턴이 끝나면 연속 더블 초기화
        {"op": "set", "path": f"players.{player_id}.consecutive_doubles", "value": 0},
    ]

    events.append(
        {
            "type": ServerEventType.TURN_ENDED,
            "player_id": player_id,
            "next_player_id": next_player_id,
            "turn": new_turn,
            "round": new_round,
        }
    )

    return events, patches
