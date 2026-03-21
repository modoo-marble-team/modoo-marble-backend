from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, TypeAlias

from app.game.enums import ServerEventType
from app.game.errors import GameActionError
from app.game.models import GameState, PendingPrompt, PromptChoice
from app.game.patch import op_set
from app.game.state import apply_patches

ActionResult: TypeAlias = tuple[list[dict], list[dict]]


@dataclass(frozen=True, slots=True)
class PromptContext:
    board_size: int
    phase_wait_prompt: str
    make_prompt: Callable[..., PendingPrompt]
    format_money: Callable[[int], str]
    apply_purchase: Callable[[GameState, int, int], ActionResult]
    apply_build: Callable[[GameState, int, int], ActionResult]
    apply_toll_payment: Callable[[GameState, int, int], ActionResult]
    apply_property_acquisition: Callable[[GameState, int, int], ActionResult]
    queue_follow_up_landing_prompt: Callable[
        [GameState, int, int, list[dict], list[dict]],
        None,
    ]
    append_landed_event: Callable[[list[dict], int, int], None]
    resolve_landing: Callable[[GameState, int, int], ActionResult]


@dataclass(frozen=True, slots=True)
class BasePromptHandler:
    prompt_type: str

    def handle(
        self,
        *,
        state: GameState,
        player_id: int,
        prompt: PendingPrompt,
        normalized_choice: str,
        payload: dict,
        patches: list[dict],
        events: list[dict],
        context: PromptContext,
    ) -> None:
        return None


@dataclass(frozen=True, slots=True)
class BuyOrSkipPromptHandler(BasePromptHandler):
    def handle(
        self,
        *,
        state: GameState,
        player_id: int,
        prompt: PendingPrompt,
        normalized_choice: str,
        payload: dict,
        patches: list[dict],
        events: list[dict],
        context: PromptContext,
    ) -> None:
        if normalized_choice != "BUY":
            return
        tile_id = int(prompt.payload.get("tileId", -1))
        action_events, action_patches = context.apply_purchase(state, player_id, tile_id)
        events.extend(action_events)
        patches.extend(action_patches)
        context.queue_follow_up_landing_prompt(
            state,
            player_id,
            tile_id,
            patches,
            events,
        )


@dataclass(frozen=True, slots=True)
class BuildOrSkipPromptHandler(BasePromptHandler):
    def handle(
        self,
        *,
        state: GameState,
        player_id: int,
        prompt: PendingPrompt,
        normalized_choice: str,
        payload: dict,
        patches: list[dict],
        events: list[dict],
        context: PromptContext,
    ) -> None:
        if normalized_choice != "BUILD":
            return
        tile_id = int(prompt.payload.get("tileId", -1))
        action_events, action_patches = context.apply_build(state, player_id, tile_id)
        events.extend(action_events)
        patches.extend(action_patches)


@dataclass(frozen=True, slots=True)
class PayTollPromptHandler(BasePromptHandler):
    def handle(
        self,
        *,
        state: GameState,
        player_id: int,
        prompt: PendingPrompt,
        normalized_choice: str,
        payload: dict,
        patches: list[dict],
        events: list[dict],
        context: PromptContext,
    ) -> None:
        tile_id = int(prompt.payload.get("tileId", -1))
        action_events, action_patches = context.apply_toll_payment(
            state,
            player_id,
            tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)

        toll = int(prompt.payload.get("toll", 0))
        if state.require_player(player_id).balance < toll:
            return

        acquisition_prompt = context.make_prompt(
            prompt_type="ACQUISITION_OR_SKIP",
            player_id=player_id,
            title=f"{prompt.payload.get('tileName', '임시')} 인수",
            message=(
                f"{prompt.payload.get('ownerName', '상대')}의 땅을 "
                f"{context.format_money(int(prompt.payload.get('acquisitionCost', 0)))}에 "
                "인수하시겠습니까?"
            ),
            choices=[
                PromptChoice(id="acquire", label="인수하기", value="ACQUIRE"),
                PromptChoice(id="skip", label="넘기기", value="SKIP"),
            ],
            payload=dict(prompt.payload),
            default_choice_value="SKIP",
        )
        patches.extend(
            [
                op_set("pending_prompt", acquisition_prompt),
                op_set("phase", context.phase_wait_prompt),
            ]
        )


@dataclass(frozen=True, slots=True)
class AcquisitionOrSkipPromptHandler(BasePromptHandler):
    def handle(
        self,
        *,
        state: GameState,
        player_id: int,
        prompt: PendingPrompt,
        normalized_choice: str,
        payload: dict,
        patches: list[dict],
        events: list[dict],
        context: PromptContext,
    ) -> None:
        if normalized_choice != "ACQUIRE":
            return
        tile_id = int(prompt.payload.get("tileId", -1))
        action_events, action_patches = context.apply_property_acquisition(
            state,
            player_id,
            tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)


@dataclass(frozen=True, slots=True)
class TravelSelectPromptHandler(BasePromptHandler):
    def handle(
        self,
        *,
        state: GameState,
        player_id: int,
        prompt: PendingPrompt,
        normalized_choice: str,
        payload: dict,
        patches: list[dict],
        events: list[dict],
        context: PromptContext,
    ) -> None:
        if normalized_choice != "CONFIRM":
            return

        raw_target_tile_id = payload.get("targetTileId")
        try:
            target_tile_id = int(raw_target_tile_id)
        except (TypeError, ValueError) as exc:
            raise GameActionError(
                code="INVALID_TILE",
                message="여행 목적지를 선택해주세요.",
            ) from exc

        if target_tile_id < 0 or target_tile_id >= context.board_size:
            raise GameActionError(
                code="INVALID_TILE",
                message="여행 목적지 범위가 올바르지 않습니다.",
            )

        current_tile_id = state.require_player(player_id).current_tile_id
        if target_tile_id == current_tile_id:
            raise GameActionError(
                code="INVALID_TILE",
                message="현재 위치와 다른 목적지를 선택해주세요.",
            )

        patches.append(op_set(f"players.{player_id}.current_tile_id", target_tile_id))
        events.append(
            {
                "type": ServerEventType.PLAYER_MOVED,
                "playerId": player_id,
                "fromTileId": current_tile_id,
                "toTileId": target_tile_id,
                "trigger": "travel",
            }
        )
        context.append_landed_event(events, player_id, target_tile_id)

        preview_state = state.clone()
        apply_patches(preview_state, patches)
        landing_events, landing_patches = context.resolve_landing(
            preview_state,
            player_id,
            target_tile_id,
        )
        events.extend(landing_events)
        patches.extend(landing_patches)


PROMPT_HANDLER_TYPES: dict[str, type[BasePromptHandler]] = {
    "BUY_OR_SKIP": BuyOrSkipPromptHandler,
    "BUILD_OR_SKIP": BuildOrSkipPromptHandler,
    "PAY_TOLL": PayTollPromptHandler,
    "ACQUISITION_OR_SKIP": AcquisitionOrSkipPromptHandler,
    "TRAVEL_SELECT": TravelSelectPromptHandler,
}


@lru_cache(maxsize=32)
def build_prompt_handler(prompt_type: str) -> BasePromptHandler:
    handler_type = PROMPT_HANDLER_TYPES.get(prompt_type, BasePromptHandler)
    return handler_type(prompt_type)
