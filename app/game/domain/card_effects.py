from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Sequence, TypeAlias

from app.game.enums import ServerEventType
from app.game.models import GameState
from app.game.patch import op_inc, op_push, op_remove, op_set

ActionResult: TypeAlias = tuple[list[dict], list[dict]]


@dataclass(frozen=True, slots=True)
class CardEffectContext:
    board_size: int
    start_salary: int
    apply_money_delta: Callable[[GameState, int, int], tuple[list[dict], list[dict]]]
    choose_random: Callable[[Sequence[Any]], Any]


@dataclass(frozen=True, slots=True)
class BaseCardEffect:
    effect_type: str

    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        return [], []


@dataclass(frozen=True, slots=True)
class GainMoneyCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        amount = int(card.get("amount", 0))
        patches, events = context.apply_money_delta(state, player_id, amount)
        return events, patches


@dataclass(frozen=True, slots=True)
class LoseMoneyCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        amount = int(card.get("amount", 0))
        patches, events = context.apply_money_delta(state, player_id, -amount)
        return events, patches


@dataclass(frozen=True, slots=True)
class MoveForwardCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        amount = int(card.get("amount", 0))
        player = state.require_player(player_id)
        from_tile = player.current_tile_id
        to_tile = (from_tile + amount) % context.board_size
        passed_start = from_tile + amount >= context.board_size

        events = [
            {
                "type": ServerEventType.PLAYER_MOVED,
                "playerId": player_id,
                "fromTileId": from_tile,
                "toTileId": to_tile,
                "trigger": "chance",
                "passGo": passed_start,
            }
        ]
        patches = [op_set(f"players.{player_id}.current_tile_id", to_tile)]
        if passed_start:
            patches.append(op_inc(f"players.{player_id}.balance", context.start_salary))
        return events, patches


@dataclass(frozen=True, slots=True)
class MoveBackwardCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        amount = int(card.get("amount", 0))
        player = state.require_player(player_id)
        from_tile = player.current_tile_id
        to_tile = (from_tile - amount) % context.board_size
        return [
            {
                "type": ServerEventType.PLAYER_MOVED,
                "playerId": player_id,
                "fromTileId": from_tile,
                "toTileId": to_tile,
                "trigger": "chance",
                "passGo": False,
            }
        ], [op_set(f"players.{player_id}.current_tile_id", to_tile)]


@dataclass(frozen=True, slots=True)
class StealPropertyCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        del context, card
        other_players = [
            (candidate_id, candidate)
            for candidate_id, candidate in state.players.items()
            if candidate_id != player_id
            and not candidate.is_bankrupt
            and candidate.owned_tiles
        ]
        if not other_players:
            return [], []

        target_id, target_player = context.choose_random(other_players)
        stolen_tile_id = context.choose_random(target_player.owned_tiles)
        patches = [
            op_set(f"tiles.{stolen_tile_id}.owner_id", player_id),
            op_set(f"tiles.{stolen_tile_id}.building_level", 0),
            op_remove(f"players.{target_id}.owned_tiles", stolen_tile_id),
            op_remove(f"players.{target_id}.building_levels", stolen_tile_id),
            op_push(f"players.{player_id}.owned_tiles", stolen_tile_id),
            op_set(f"players.{player_id}.building_levels.{stolen_tile_id}", 0),
        ]
        events = [
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "chance": {
                    "type": "STEAL_PROPERTY",
                    "fromPlayerId": target_id,
                    "tileId": stolen_tile_id,
                },
            }
        ]
        return events, patches


@dataclass(frozen=True, slots=True)
class GivePropertyCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        del context, card
        player = state.require_player(player_id)
        if not player.owned_tiles:
            return [], []

        receivers = [
            candidate_id
            for candidate_id, candidate in state.players.items()
            if candidate_id != player_id and not candidate.is_bankrupt
        ]
        if not receivers:
            return [], []

        given_tile_id = context.choose_random(player.owned_tiles)
        receiver_id = int(context.choose_random(receivers))
        patches = [
            op_set(f"tiles.{given_tile_id}.owner_id", receiver_id),
            op_set(f"tiles.{given_tile_id}.building_level", 0),
            op_remove(f"players.{player_id}.owned_tiles", given_tile_id),
            op_remove(f"players.{player_id}.building_levels", given_tile_id),
            op_push(f"players.{receiver_id}.owned_tiles", given_tile_id),
            op_set(f"players.{receiver_id}.building_levels.{given_tile_id}", 0),
        ]
        events = [
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "chance": {
                    "type": "GIVE_PROPERTY",
                    "toPlayerId": receiver_id,
                    "tileId": given_tile_id,
                },
            }
        ]
        return events, patches


CARD_EFFECT_TYPES: dict[str, type[BaseCardEffect]] = {
    "GAIN_MONEY": GainMoneyCardEffect,
    "LOSE_MONEY": LoseMoneyCardEffect,
    "MOVE_FORWARD": MoveForwardCardEffect,
    "MOVE_BACKWARD": MoveBackwardCardEffect,
    "STEAL_PROPERTY": StealPropertyCardEffect,
    "GIVE_PROPERTY": GivePropertyCardEffect,
}


@lru_cache(maxsize=32)
def build_card_effect(effect_type: str) -> BaseCardEffect:
    effect_class = CARD_EFFECT_TYPES.get(effect_type, BaseCardEffect)
    return effect_class(effect_type)
