from __future__ import annotations

from typing import Callable, TypeAlias

from app.game.actions.end_turn import process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.enums import ActionType
from app.game.errors import GameActionError
from app.game.models import GameState
from app.game.rules import (
    process_buy_property_action,
    process_city_build_action,
    process_prompt_response,
    process_turn_sell_property_action,
)

ActionResult: TypeAlias = tuple[list[dict], list[dict]]
ActionHandler: TypeAlias = Callable[[GameState, int, dict], ActionResult]


def _get_payload(data: dict) -> dict:
    raw = data.get("payload")
    return raw if isinstance(raw, dict) else {}


def _parse_tile_id(payload: dict) -> int:
    return int(payload.get("tileId", -1))


def _parse_building_level(payload: dict) -> int | None:
    raw = payload.get("buildingLevel")
    if isinstance(raw, (int, str)) and str(raw).strip() != "":
        return int(raw)
    return None


def _parse_travel_target(payload: dict) -> int:
    raw_target = payload.get("targetTileId")
    if raw_target is None:
        raw_target = payload.get("toTileId")
    if raw_target is None:
        raw_target = payload.get("toIndex")
    try:
        return int(raw_target)
    except (TypeError, ValueError) as exc:
        raise GameActionError(
            code="INVALID_TILE",
            message="여행 목적지를 선택해주세요.",
        ) from exc


def _handle_roll_dice(state: GameState, user_id: int, data: dict) -> ActionResult:
    return process_roll_dice(state, user_id)


def _handle_buy_property(state: GameState, user_id: int, data: dict) -> ActionResult:
    payload = _get_payload(data)
    return process_buy_property_action(
        state,
        player_id=user_id,
        tile_id=_parse_tile_id(payload),
    )


def _handle_sell_property(state: GameState, user_id: int, data: dict) -> ActionResult:
    payload = _get_payload(data)
    return process_turn_sell_property_action(
        state,
        player_id=user_id,
        tile_id=_parse_tile_id(payload),
        building_level=_parse_building_level(payload),
    )


def _handle_city_build(state: GameState, user_id: int, data: dict) -> ActionResult:
    payload = _get_payload(data)
    return process_city_build_action(
        state,
        player_id=user_id,
        tile_id=_parse_tile_id(payload),
    )


def _handle_end_turn(state: GameState, user_id: int, data: dict) -> ActionResult:
    return process_end_turn(state, user_id)


def _handle_travel(state: GameState, user_id: int, data: dict) -> ActionResult:
    pending_prompt = state.pending_prompt
    if pending_prompt is None or pending_prompt.type != "TRAVEL_SELECT":
        raise GameActionError(
            code="INVALID_PHASE",
            message="여행지 선택 대기 상태가 아닙니다.",
        )
    payload = _get_payload(data)
    target_tile_id = _parse_travel_target(payload)
    return process_prompt_response(
        state,
        player_id=user_id,
        prompt_id=pending_prompt.prompt_id,
        choice="CONFIRM",
        payload={"targetTileId": target_tile_id},
    )


ACTION_HANDLERS: dict[ActionType, ActionHandler] = {
    ActionType.ROLL_DICE: _handle_roll_dice,
    ActionType.BUY_PROPERTY: _handle_buy_property,
    ActionType.SELL_PROPERTY: _handle_sell_property,
    ActionType.CITY_BUILD: _handle_city_build,
    ActionType.END_TURN: _handle_end_turn,
    ActionType.TRAVEL: _handle_travel,
}


def dispatch_game_action(
    state: GameState,
    *,
    user_id: int,
    action_type: str,
    data: dict,
) -> ActionResult:
    """액션 타입에 따라 적절한 게임 액션 처리 함수를 실행합니다.

    Returns:
        (events, patches) 튜플

    Raises:
        GameActionError: 지원하지 않는 액션이거나 처리 중 오류가 발생한 경우
    """
    handler = ACTION_HANDLERS.get(action_type)  # type: ignore[call-overload]
    if handler is None:
        raise GameActionError(
            code="UNKNOWN_ACTION",
            message=f"지원하지 않는 액션입니다: {action_type}",
        )
    return handler(state, user_id, data)
