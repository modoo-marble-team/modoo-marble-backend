from __future__ import annotations

from app.game.enums import PlayerState, ServerEventType
from app.game.errors import GameActionError
from app.game.rules import PHASE_GAME_OVER, PHASE_WAIT_PROMPT, PHASE_WAIT_ROLL
from app.game.schemas import GameState

MAX_ROUNDS = 20


def get_next_player_id(state: GameState, current_player_id: int) -> int:
    """
    다음 턴을 할 플레이어 ID를 반환한다.
    파산한 플레이어는 건너뛴다.
    """
    # 파산하지 않은 플레이어만 turnOrder 순으로 정렬
    active_players = sorted(
        [
            p
            for p in state["players"].values()
            if p["playerState"] != PlayerState.BANKRUPT
        ],
        key=lambda p: p["turnOrder"],
    )

    if not active_players:
        return current_player_id

    current_order = state["players"][str(current_player_id)]["turnOrder"]

    # 현재보다 order가 높은 첫 번째 플레이어
    for p in active_players:
        if p["turnOrder"] > current_order:
            return p["playerId"]

    # 없으면 처음으로 돌아감 (한 바퀴 완료)
    return active_players[0]["playerId"]


def _find_winner(state: GameState) -> dict:
    """잔액이 가장 많은 플레이어를 승자로 반환한다."""
    players = list(state["players"].values())
    winner = max(players, key=lambda p: p["balance"])
    return {
        "playerId": winner["playerId"],
        "nickname": winner["nickname"],
        "balance": winner["balance"],
    }


def process_end_turn(
    state: GameState,
    player_id: int,
) -> tuple[list[dict], list[dict]]:
    if state["current_player_id"] != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="It is not your turn.")
    if state["status"] != "playing":
        raise GameActionError(code="INVALID_PHASE", message="Game is not active.")
    if state["phase"] == PHASE_WAIT_PROMPT:
        raise GameActionError(
            code="INVALID_PHASE", message="Resolve the pending prompt first."
        )

    active_players = [
        player
        for player in state["players"].values()
        if player["playerState"] != PlayerState.BANKRUPT
    ]
    if len(active_players) <= 1:
        winner = _find_winner(state)
        return [
            {
                "type": ServerEventType.GAME_OVER,
                "reason": "last_player_standing",
                "winner": winner,
            }
        ], [
            {"op": "set", "path": "status", "value": "finished"},
            {"op": "set", "path": "phase", "value": PHASE_GAME_OVER},
            {"op": "set", "path": "pending_prompt", "value": None},
        ]

    next_player_id = get_next_player_id(state, player_id)
    current_order = state["players"][str(player_id)]["turnOrder"]
    next_order = state["players"][str(next_player_id)]["turnOrder"]

    new_turn = state["turn"] + 1
    new_round = state["round"] + 1 if next_order <= current_order else state["round"]

    patches = [
        {"op": "set", "path": "current_player_id", "value": next_player_id},
        {"op": "set", "path": "turn", "value": new_turn},
        {"op": "set", "path": "round", "value": new_round},
        {"op": "set", "path": "phase", "value": PHASE_WAIT_ROLL},
        {"op": "set", "path": "pending_prompt", "value": None},
        # 턴이 끝나면 연속 더블 초기화
        {"op": "set", "path": f"players.{player_id}.consecutiveDoubles", "value": 0},
    ]

    events = [
        {
            "type": ServerEventType.TURN_ENDED,
            "playerId": player_id,
            "nextPlayerId": next_player_id,
            "turn": new_turn,
            "round": new_round,
        }
    ]

    # ── 20라운드 종료 조건 ───────────────────────────────
    if new_round > MAX_ROUNDS:
        patches.append({"op": "set", "path": "status", "value": "finished"})
        patches.append({"op": "set", "path": "phase", "value": PHASE_GAME_OVER})
        winner = _find_winner(state)
        events.append(
            {
                "type": ServerEventType.GAME_OVER,
                "reason": "max_rounds",
                "winner": winner,
            }
        )

    return events, patches
