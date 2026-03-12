from __future__ import annotations

from app.game.enums import PlayerState, ServerEventType
from app.game.errors import GameActionError
from app.game.rules import PHASE_GAME_OVER, PHASE_WAIT_PROMPT, PHASE_WAIT_ROLL
from app.game.schemas import GameState


def get_next_player_id(state: GameState, current_player_id: int) -> int:
    active_players = sorted(
        [p for p in state["players"].values() if p["state"] != PlayerState.BANKRUPT],
        key=lambda p: p["turn_order"],
    )

    if not active_players:
        return current_player_id

    current_order = state["players"][str(current_player_id)]["turn_order"]
    for player in active_players:
        if player["turn_order"] > current_order:
            return player["user_id"]

    return active_players[0]["user_id"]


def process_end_turn(
    state: GameState,
    player_id: int,
) -> tuple[list[dict], list[dict]]:
    if state["current_player_id"] != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="It is not your turn.")
    if state["status"] != "playing":
        raise GameActionError(code="INVALID_PHASE", message="Game is not active.")
    if state["phase"] == PHASE_WAIT_PROMPT:
        raise GameActionError(code="INVALID_PHASE", message="Resolve the pending prompt first.")

    active_players = [
        player for player in state["players"].values() if player["state"] != PlayerState.BANKRUPT
    ]
    if len(active_players) <= 1:
        return [
            {
                "type": ServerEventType.GAME_OVER,
                "player_id": player_id,
            }
        ], [
            {"op": "set", "path": "status", "value": "finished"},
            {"op": "set", "path": "phase", "value": PHASE_GAME_OVER},
            {"op": "set", "path": "pending_prompt", "value": None},
        ]

    next_player_id = get_next_player_id(state, player_id)
    current_order = state["players"][str(player_id)]["turn_order"]
    next_order = state["players"][str(next_player_id)]["turn_order"]
    new_turn = state["turn"] + 1
    new_round = state["round"] + 1 if next_order <= current_order else state["round"]

    patches = [
        {"op": "set", "path": "current_player_id", "value": next_player_id},
        {"op": "set", "path": "turn", "value": new_turn},
        {"op": "set", "path": "round", "value": new_round},
        {"op": "set", "path": "phase", "value": PHASE_WAIT_ROLL},
        {"op": "set", "path": "pending_prompt", "value": None},
        {"op": "set", "path": f"players.{player_id}.consecutive_doubles", "value": 0},
    ]

    events = [
        {
            "type": ServerEventType.TURN_ENDED,
            "player_id": player_id,
            "next_player_id": next_player_id,
            "turn": new_turn,
            "round": new_round,
        }
    ]
    return events, patches

