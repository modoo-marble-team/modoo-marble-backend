"""카드 한 장이 실행될 때의 효과를 모아 둔 모듈."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeAlias

from app.game.domain.card_descriptions import render_card_text
from app.game.enums import ServerEventType
from app.game.models import GameState
from app.game.patch import op_inc, op_push, op_remove, op_set

ActionResult: TypeAlias = tuple[list[dict], list[dict]]


@dataclass(frozen=True, slots=True)
class CardEffectContext:
    # 카드 효과가 외부 기능을 호출할 때 쓰는 함수 묶음.
    board_size: int
    start_salary: int
    apply_money_delta: Callable[[GameState, int, int], tuple[list[dict], list[dict]]]
    choose_random: Callable[[Sequence[Any]], Any]
    player_name: Callable[[GameState, int], str]
    tile_name: Callable[[int], str]
    get_object_particle: Callable[[str], str]


def _build_card_resolution_payload(
    card: dict[str, Any],
    *,
    description: str,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "type": card["type"],
        "power": card.get("amount", 0),
        "description": description,
    }
    for key, value in card.items():
        if key in {"type", "amount", "description", "failed_description"}:
            continue
        payload[key] = value
    if extra_payload:
        payload.update(extra_payload)
    return payload


@dataclass(frozen=True, slots=True)
class BaseCardEffect:
    # 모든 카드 효과의 공통 인터페이스.
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
        # 앞으로 이동하다가 출발 칸을 넘으면 월급도 같이 지급한다.
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
        # 뒤로 이동은 월급 없이 위치만 바꾼다.
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
        # 다른 플레이어의 땅 하나를 무작위로 가져온다.
        owned_tiles = [
            (candidate_id, tile_id)
            for candidate_id, candidate in state.players.items()
            if candidate_id != player_id
            and not candidate.is_bankrupt
            for tile_id in candidate.owned_tiles
        ]
        if not owned_tiles:
            return [
                {
                    "type": ServerEventType.CHANCE_RESOLVED,
                    "playerId": player_id,
                    "chance": _build_card_resolution_payload(
                        card,
                        description=render_card_text(
                            card,
                            template_key="failed_description",
                        )
                        or render_card_text(card),
                    ),
                }
            ], []

        target_id, stolen_tile_id = context.choose_random(owned_tiles)
        target_name = context.player_name(state, target_id)
        tile_name = context.tile_name(stolen_tile_id)
        description = render_card_text(
            card,
            variables={
                "player": target_name,
                "property": tile_name,
                "suffix": context.get_object_particle(tile_name),
            },
        )
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
                "chance": _build_card_resolution_payload(
                    card,
                    description=description,
                    extra_payload={
                        "fromPlayerId": target_id,
                        "tileId": stolen_tile_id,
                        "property": tile_name,
                    },
                ),
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
        # 내 땅 하나를 다른 플레이어에게 넘긴다.
        player = state.require_player(player_id)
        if not player.owned_tiles:
            return [
                {
                    "type": ServerEventType.CHANCE_RESOLVED,
                    "playerId": player_id,
                    "chance": _build_card_resolution_payload(
                        card,
                        description=render_card_text(
                            card,
                            template_key="failed_description",
                        )
                        or render_card_text(card),
                    ),
                }
            ], []

        receivers = [
            candidate_id
            for candidate_id, candidate in state.players.items()
            if candidate_id != player_id and not candidate.is_bankrupt
        ]
        if not receivers:
            return [
                {
                    "type": ServerEventType.CHANCE_RESOLVED,
                    "playerId": player_id,
                    "chance": _build_card_resolution_payload(
                        card,
                        description=render_card_text(
                            card,
                            template_key="failed_description",
                        )
                        or render_card_text(card),
                    ),
                }
            ], []

        given_tile_id = context.choose_random(player.owned_tiles)
        receiver_id = context.choose_random(receivers)
        receiver_name = context.player_name(state, receiver_id)
        tile_name = context.tile_name(given_tile_id)
        description = render_card_text(
            card,
            variables={
                "player": receiver_name,
                "property": tile_name,
                "suffix": context.get_object_particle(tile_name),
            },
        )
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
                "chance": _build_card_resolution_payload(
                    card,
                    description=description,
                    extra_payload={
                        "toPlayerId": receiver_id,
                        "tileId": given_tile_id,
                        "property": tile_name,
                    },
                ),
            }
        ]
        return events, patches


@dataclass(frozen=True, slots=True)
class TollMultiplierCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        del player_id, context
        duration = int(card.get("duration", 3))
        multiplier = float(card.get("multiplier", 1))
        del state
        return [], [
            op_set("global_effects.toll_multiplier_turns_remaining", duration),
            op_set("global_effects.toll_multiplier_value", multiplier),
        ]


@dataclass(frozen=True, slots=True)
class PriceMultiplierCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        del player_id, context, state
        duration = int(card.get("duration", 3))
        multiplier = float(card.get("multiplier", 1))
        return [], [
            op_set("global_effects.price_multiplier_turns_remaining", duration),
            op_set("global_effects.price_multiplier_value", multiplier),
        ]


@dataclass(frozen=True, slots=True)
class ExtraTurnCardEffect(BaseCardEffect):
    def apply(
        self,
        *,
        state: GameState,
        player_id: int,
        card: dict,
        context: CardEffectContext,
    ) -> ActionResult:
        del state, context
        duration = int(card.get("duration", 1))
        return [], [
            op_inc(f"players.{player_id}.extra_turn_effect_turns_remaining", duration)
        ]


CARD_EFFECT_TYPES: dict[str, type[BaseCardEffect]] = {
    "GAIN_MONEY": GainMoneyCardEffect,
    "LOSE_MONEY": LoseMoneyCardEffect,
    "MOVE_FORWARD": MoveForwardCardEffect,
    "MOVE_BACKWARD": MoveBackwardCardEffect,
    "STEAL_PROPERTY": StealPropertyCardEffect,
    "GIVE_PROPERTY": GivePropertyCardEffect,
    "TOLL_MULTIPLIER": TollMultiplierCardEffect,
    "PRICE_MULTIPLIER": PriceMultiplierCardEffect,
    "EXTRA_TURN": ExtraTurnCardEffect,
}


@lru_cache(maxsize=32)
def build_card_effect(effect_type: str) -> BaseCardEffect:
    # 카드 타입 문자열을 실제 효과 객체로 바꾼다.
    effect_class = CARD_EFFECT_TYPES.get(effect_type, BaseCardEffect)
    return effect_class(effect_type)
