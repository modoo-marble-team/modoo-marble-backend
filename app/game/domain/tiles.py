from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Sequence, TypeAlias

from app.game.domain.ruleset import TileDefinition
from app.game.enums import PlayerState, ServerEventType, TileType
from app.game.models import GameState, PromptChoice, TileGameState
from app.game.patch import op_inc, op_set
from app.game.state import apply_patches

ActionResult: TypeAlias = tuple[list[dict], list[dict]]


@dataclass(frozen=True, slots=True)
class LandingContext:
    phase_wait_prompt: str
    phase_resolving: str
    phase_game_over: str
    max_building_level: int
    island_tile_id: int
    chance_cards: list[dict]
    event_cards: list[dict]
    choose_random: Callable[[Sequence[Any]], Any]
    make_prompt: Callable[..., object]
    get_object_particle: Callable[[str], str]
    format_money: Callable[[int], str]
    get_build_stage_name: Callable[[int], str]
    get_toll_amount: Callable[[int, int], int]
    get_acquisition_cost: Callable[[int, int], int]
    player_name: Callable[[GameState, int], str]
    apply_card: Callable[[GameState, int, dict], ActionResult]
    append_landed_event: Callable[[list[dict], int, int], None]
    queue_follow_up_landing_resolution: Callable[
        [GameState, int, int, list[dict], list[dict], bool],
        None,
    ]


@dataclass(frozen=True, slots=True)
class BaseTile:
    definition: TileDefinition

    def on_land(
        self,
        *,
        state: GameState,
        player_id: int,
        tile_state: TileGameState | None,
        events: list[dict],
        patches: list[dict],
        context: LandingContext,
    ) -> None:
        return None


@dataclass(frozen=True, slots=True)
class PropertyTile(BaseTile):
    def on_land(
        self,
        *,
        state: GameState,
        player_id: int,
        tile_state: TileGameState | None,
        events: list[dict],
        patches: list[dict],
        context: LandingContext,
    ) -> None:
        if tile_state is None:
            return

        owner_id = tile_state.owner_id
        building_level = tile_state.building_level
        tile_id = self.definition.tile_id

        if owner_id is None:
            prompt = context.make_prompt(
                prompt_type="BUY_OR_SKIP",
                player_id=player_id,
                title=f"{self.definition.name} 구매",
                message=(
                    f"{self.definition.name}에 도착했습니다. "
                    f"{self.definition.name}"
                    f"{context.get_object_particle(self.definition.name)} "
                    f"{context.format_money(self.definition.price)}에 구매하시겠습니까?"
                ),
                choices=[
                    PromptChoice(id="buy", label="구매", value="BUY"),
                    PromptChoice(id="skip", label="건너뛰기", value="SKIP"),
                ],
                payload={
                    "tileId": tile_id,
                    "tileName": self.definition.name,
                    "price": self.definition.price,
                    "buildingLevel": building_level,
                },
                default_choice_value="SKIP",
            )
            patches.extend(
                [
                    op_set("pending_prompt", prompt),
                    op_set("phase", context.phase_wait_prompt),
                ]
            )
            return

        if owner_id == player_id and building_level < context.max_building_level:
            build_cost = self.definition.build_costs[building_level]
            next_toll = context.get_toll_amount(tile_id, building_level + 1)
            next_stage_name = context.get_build_stage_name(building_level + 1)
            prompt = context.make_prompt(
                prompt_type="BUILD_OR_SKIP",
                player_id=player_id,
                title=f"{self.definition.name} {next_stage_name} 건설",
                message=(
                    f"{self.definition.name}에 {next_stage_name}"
                    f"{context.get_object_particle(next_stage_name)} "
                    f"{context.format_money(build_cost)}에 건설하시겠습니까?"
                ),
                choices=[
                    PromptChoice(id="build", label="건설", value="BUILD"),
                    PromptChoice(id="skip", label="건너뛰기", value="SKIP"),
                ],
                payload={
                    "tileId": tile_id,
                    "tileName": self.definition.name,
                    "price": build_cost,
                    "buildCost": build_cost,
                    "buildingLevel": building_level,
                    "nextToll": next_toll,
                },
                default_choice_value="SKIP",
            )
            patches.extend(
                [
                    op_set("pending_prompt", prompt),
                    op_set("phase", context.phase_wait_prompt),
                ]
            )
            return

        if owner_id != player_id:
            toll = context.get_toll_amount(tile_id, building_level)
            acquisition_cost = context.get_acquisition_cost(tile_id, building_level)
            owner_name = context.player_name(state, owner_id)
            prompt = context.make_prompt(
                prompt_type="PAY_TOLL",
                player_id=player_id,
                title=f"{self.definition.name} 통행료",
                message=(
                    f"{owner_name}의 {self.definition.name}입니다. "
                    f"먼저 통행료 {context.format_money(toll)}를 지불한 뒤 "
                    "인수 여부를 결정합니다."
                ),
                choices=[PromptChoice(id="pay", label="확인", value="PAY_TOLL")],
                payload={
                    "tileId": tile_id,
                    "tileName": self.definition.name,
                    "ownerId": owner_id,
                    "ownerName": owner_name,
                    "acquisitionCost": acquisition_cost,
                    "toll": toll,
                    "amount": toll,
                    "buildingLevel": building_level,
                },
                default_choice_value="PAY_TOLL",
            )
            patches.extend(
                [
                    op_set("pending_prompt", prompt),
                    op_set("phase", context.phase_wait_prompt),
                ]
            )


@dataclass(frozen=True, slots=True)
class MoveToIslandTile(BaseTile):
    def on_land(
        self,
        *,
        state: GameState,
        player_id: int,
        tile_state: TileGameState | None,
        events: list[dict],
        patches: list[dict],
        context: LandingContext,
    ) -> None:
        patches.extend(
            [
                op_set(f"players.{player_id}.current_tile_id", context.island_tile_id),
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
                    "fromTileId": self.definition.tile_id,
                    "toTileId": context.island_tile_id,
                    "trigger": "move_to_island",
                },
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED,
                    "playerId": player_id,
                    "playerState": PlayerState.LOCKED,
                    "reason": "move_to_island",
                },
            ]
        )


@dataclass(frozen=True, slots=True)
class TravelTile(BaseTile):
    def on_land(
        self,
        *,
        state: GameState,
        player_id: int,
        tile_state: TileGameState | None,
        events: list[dict],
        patches: list[dict],
        context: LandingContext,
    ) -> None:
        prompt = context.make_prompt(
            prompt_type="TRAVEL_SELECT",
            player_id=player_id,
            title="여행",
            message="이동할 목적지를 선택해주세요.",
            choices=[
                PromptChoice(id="confirm", label="선택", value="CONFIRM"),
                PromptChoice(id="skip", label="건너뛰기", value="SKIP"),
            ],
            payload={
                "tileId": self.definition.tile_id,
                "tileName": self.definition.name,
            },
            default_choice_value="SKIP",
        )
        patches.extend(
            [
                op_set("pending_prompt", prompt),
                op_set("phase", context.phase_wait_prompt),
            ]
        )


@dataclass(frozen=True, slots=True)
class EventTile(BaseTile):
    def on_land(
        self,
        *,
        state: GameState,
        player_id: int,
        tile_state: TileGameState | None,
        events: list[dict],
        patches: list[dict],
        context: LandingContext,
    ) -> None:
        card = context.choose_random(context.event_cards)
        card_events, card_patches = context.apply_card(state, player_id, card)
        patches.extend(card_patches)
        events.extend(card_events)
        events.append(
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "tileId": self.definition.tile_id,
                "chance": {
                    "type": card["type"],
                    "power": card.get("amount", 0),
                    "description": card["description"],
                },
            }
        )


@dataclass(frozen=True, slots=True)
class ChanceTile(BaseTile):
    def on_land(
        self,
        *,
        state: GameState,
        player_id: int,
        tile_state: TileGameState | None,
        events: list[dict],
        patches: list[dict],
        context: LandingContext,
    ) -> None:
        card = context.choose_random(context.chance_cards)
        card_events, card_patches = context.apply_card(state, player_id, card)
        chance_event = {
            "type": ServerEventType.CHANCE_RESOLVED,
            "playerId": player_id,
            "tileId": self.definition.tile_id,
            "chance": {
                "type": card["type"],
                "power": card.get("amount", 0),
                "description": card["description"],
            },
        }

        if card["type"] in {"MOVE_FORWARD", "MOVE_BACKWARD"}:
            events.append(chance_event)
            events.extend(card_events)
            patches.extend(card_patches)

            preview_state = state.clone()
            apply_patches(preview_state, patches)
            destination_tile_id = preview_state.require_player(player_id).current_tile_id
            context.queue_follow_up_landing_resolution(
                state,
                player_id,
                destination_tile_id,
                patches,
                events,
                True,
            )
            return

        patches.extend(card_patches)
        events.extend(card_events)
        if not any(
            event.get("type") == ServerEventType.CHANCE_RESOLVED
            for event in card_events
        ):
            events.append(chance_event)


@dataclass(frozen=True, slots=True)
class AiTile(BaseTile):
    def on_land(
        self,
        *,
        state: GameState,
        player_id: int,
        tile_state: TileGameState | None,
        events: list[dict],
        patches: list[dict],
        context: LandingContext,
    ) -> None:
        events.append(
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "tileId": self.definition.tile_id,
                "chance": {"type": "AI_SKIPPED", "power": 0},
            }
        )


TILE_HANDLER_TYPES: dict[TileType, type[BaseTile]] = {
    TileType.PROPERTY: PropertyTile,
    TileType.MOVE_TO_ISLAND: MoveToIslandTile,
    TileType.TRAVEL: TravelTile,
    TileType.EVENT: EventTile,
    TileType.CHANCE: ChanceTile,
    TileType.AI: AiTile,
}


@lru_cache(maxsize=128)
def build_tile_handler(definition: TileDefinition) -> BaseTile:
    handler_type = TILE_HANDLER_TYPES.get(definition.tile_type, BaseTile)
    return handler_type(definition)
