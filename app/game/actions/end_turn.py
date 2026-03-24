from __future__ import annotations

from app.game.enums import ServerEventType
from app.game.errors import GameActionError
from app.game.game_rules import MAX_ROUNDS
from app.game.models import GameState
from app.game.patch import op_set
from app.game.rules import (
    build_rankings_payload,
    PHASE_GAME_OVER,
    PHASE_RESOLVING,
    PHASE_WAIT_PROMPT,
    PHASE_WAIT_ROLL,
    find_winner_by_assets,
)


def get_next_player_id(state: GameState, current_player_id: int) -> int:
    active_players = state.active_players()
    if not active_players:
        return current_player_id

    current_order = state.require_player(current_player_id).turn_order
    for player in active_players:
        if player.turn_order > current_order:
            return player.player_id
    return active_players[0].player_id


def _find_winner(state: GameState) -> dict:
    winner = find_winner_by_assets(state)
    if winner is None:
        raise GameActionError(
            code="INVALID_PHASE", message="승자를 계산할 수 없습니다."
        )
    return winner


def _tick_global_effect(
    *,
    turns_path: str,
    turns_remaining: int,
    reset_path: str,
    reset_value: float,
) -> list[dict]:
    if turns_remaining <= 0:
        return []

    next_turns = turns_remaining - 1
    patches = [op_set(turns_path, next_turns)]
    if next_turns == 0:
        patches.append(op_set(reset_path, reset_value))
    return patches


def _global_effect_tick_patches(state: GameState) -> list[dict]:
    patches: list[dict] = []
    patches.extend(
        _tick_global_effect(
            turns_path="global_effects.toll_multiplier_turns_remaining",
            turns_remaining=state.global_effects.toll_multiplier_turns_remaining,
            reset_path="global_effects.toll_multiplier_value",
            reset_value=1.0,
        )
    )
    patches.extend(
        _tick_global_effect(
            turns_path="global_effects.price_multiplier_turns_remaining",
            turns_remaining=state.global_effects.price_multiplier_turns_remaining,
            reset_path="global_effects.price_multiplier_value",
            reset_value=1.0,
        )
    )
    return patches


def process_end_turn(
    state: GameState,
    player_id: int,
) -> tuple[list[dict], list[dict]]:
    if state.current_player_id != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="내 턴이 아닙니다.")
    if state.status != "playing":
        raise GameActionError(
            code="INVALID_PHASE", message="진행 중인 게임이 아닙니다."
        )
    if state.phase == PHASE_WAIT_PROMPT:
        raise GameActionError(
            code="INVALID_PHASE",
            message="대기 중인 프롬프트를 먼저 처리해주세요.",
        )

    if state.phase == PHASE_WAIT_ROLL:
        raise GameActionError(
            code="INVALID_PHASE",
            message="주사위를 먼저 굴려야 합니다.",
        )
    if state.phase != PHASE_RESOLVING:
        raise GameActionError(
            code="INVALID_PHASE",
            message="현재 상태에서는 턴을 종료할 수 없습니다.",
        )

    if len(state.active_players()) <= 1:
        winner = _find_winner(state)
        rankings = build_rankings_payload(state)
        return [
            {
                "type": ServerEventType.GAME_OVER,
                "reason": "last_player_standing",
                "winner": winner,
                "rankings": rankings,
            }
        ], [
            op_set("status", "finished"),
            op_set("phase", PHASE_GAME_OVER),
            op_set("pending_prompt", None),
            op_set("winner_id", winner["playerId"]),
        ]

    new_turn = state.turn + 1
    current_player = state.require_player(player_id)
    bonus_turn = current_player.consecutive_doubles > 0
    global_effect_patches = _global_effect_tick_patches(state)

    if bonus_turn:
        patches = [
            op_set("current_player_id", player_id),
            op_set("turn", new_turn),
            op_set("round", state.round),
            op_set("phase", PHASE_WAIT_ROLL),
            op_set("pending_prompt", None),
        ]
        patches.extend(global_effect_patches)
        events = [
            {
                "type": ServerEventType.TURN_ENDED,
                "playerId": player_id,
                "nextPlayerId": player_id,
                "turn": new_turn,
                "round": state.round,
                "bonusTurn": True,
                "reason": "double_roll",
            }
        ]
        return events, patches

    extra_turn_bonus = (
        current_player.extra_turn_effect_turns_remaining > 0
        and not current_player.extra_turn_effect_active
    )

    if extra_turn_bonus:
        patches = [
            op_set("current_player_id", player_id),
            op_set("turn", new_turn),
            op_set("round", state.round),
            op_set("phase", PHASE_WAIT_ROLL),
            op_set("pending_prompt", None),
            op_set(
                f"players.{player_id}.extra_turn_effect_turns_remaining",
                current_player.extra_turn_effect_turns_remaining - 1,
            ),
            op_set(f"players.{player_id}.extra_turn_effect_active", True),
        ]
        patches.extend(global_effect_patches)
        events = [
            {
                "type": ServerEventType.TURN_ENDED,
                "playerId": player_id,
                "nextPlayerId": player_id,
                "turn": new_turn,
                "round": state.round,
                "bonusTurn": True,
                "reason": "extra_turn_effect",
            }
        ]
        return events, patches

    next_player_id = get_next_player_id(state, player_id)
    next_order = state.require_player(next_player_id).turn_order
    new_round = (
        state.round + 1 if next_order <= current_player.turn_order else state.round
    )

    patches = [
        op_set("current_player_id", next_player_id),
        op_set("turn", new_turn),
        op_set("round", new_round),
        op_set("phase", PHASE_WAIT_ROLL),
        op_set("pending_prompt", None),
        op_set(f"players.{player_id}.consecutive_doubles", 0),
        op_set(f"players.{player_id}.extra_turn_effect_active", False),
    ]
    patches.extend(global_effect_patches)
    events = [
        {
            "type": ServerEventType.TURN_ENDED,
            "playerId": player_id,
            "nextPlayerId": next_player_id,
            "turn": new_turn,
            "round": new_round,
        }
    ]

    if new_round > MAX_ROUNDS:
        winner = _find_winner(state)
        rankings = build_rankings_payload(state)
        patches.extend(
            [
                op_set("status", "finished"),
                op_set("phase", PHASE_GAME_OVER),
                op_set("winner_id", winner["playerId"]),
            ]
        )
        events.append(
            {
                "type": ServerEventType.GAME_OVER,
                "reason": "max_rounds",
                "winner": winner,
                "rankings": rankings,
            }
        )

    return events, patches
