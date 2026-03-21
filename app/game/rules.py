"""게임 규칙의 진입점을 모아 둔 모듈.

이 파일은 어떤 규칙을 호출할지 고르는 역할을 하고,
세부 계산은 domain 하위 모듈에 위임한다.
"""

from __future__ import annotations

import random
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from app.game.board import BOARD_SIZE, TILE_MAP
from app.game.domain.card_effects import CardEffectContext, build_card_effect
from app.game.domain.prompts import PromptContext, build_prompt_handler
from app.game.domain.property_actions import (
    PropertyActionContext,
)
from app.game.domain.property_actions import (
    apply_build as apply_build_action,
)
from app.game.domain.property_actions import (
    apply_property_acquisition as apply_property_acquisition_action,
)
from app.game.domain.property_actions import (
    apply_purchase as apply_purchase_action,
)
from app.game.domain.property_actions import (
    apply_sell_property as apply_sell_property_action,
)
from app.game.domain.property_actions import (
    apply_toll_payment as apply_toll_payment_action,
)
from app.game.domain.tiles import LandingContext, build_tile_handler
from app.game.enums import PlayerState, ServerEventType
from app.game.errors import GameActionError
from app.game.game_rules import (
    ACQUISITION_PRICE_MULTIPLIER,
    ISLAND_TILE_ID,
    SELL_BUILD_COST_REFUND_RATIO,
    SELL_PURCHASE_PRICE_REFUND_RATIO,
    START_SALARY,
)
from app.game.game_rules import (
    BUILDING_STAGE_LABELS as _RULESET_BUILDING_STAGE_LABELS,
)
from app.game.game_rules import (
    CHANCE_CARD_POOL as _RULESET_CHANCE_CARD_POOL,
)
from app.game.game_rules import (
    EVENT_CARD_POOL as _RULESET_EVENT_CARD_POOL,
)
from app.game.game_rules import (
    MAX_BUILDING_LEVEL as _RULESET_MAX_BUILDING_LEVEL,
)
from app.game.game_rules import (
    PROMPT_TIMEOUT_SECONDS as _RULESET_PROMPT_TIMEOUT_SECONDS,
)
from app.game.models import GameState, PendingPrompt, PromptChoice
from app.game.patch import op_inc, op_push, op_set
from app.game.state import apply_patches

PHASE_WAIT_ROLL = "WAIT_ROLL"
PHASE_RESOLVING = "RESOLVING"
PHASE_WAIT_PROMPT = "WAIT_PROMPT"
PHASE_GAME_OVER = "GAME_OVER"

PROMPT_CHOICE_CANONICAL_MAP: dict[str, tuple[str, ...]] = {
    "BUY_OR_SKIP": ("BUY", "SKIP"),
    "BUILD_OR_SKIP": ("BUILD", "SKIP"),
    "PAY_TOLL": ("PAY_TOLL",),
    "ACQUISITION_OR_SKIP": ("ACQUIRE", "SKIP"),
    "CONFIRM_ONLY": ("CONFIRM",),
    "TRAVEL_SELECT": ("CONFIRM", "SKIP"),
}

BUILDING_STAGE_LABELS = dict(_RULESET_BUILDING_STAGE_LABELS)
PROMPT_TIMEOUT_SECONDS = _RULESET_PROMPT_TIMEOUT_SECONDS
MAX_BUILDING_LEVEL = _RULESET_MAX_BUILDING_LEVEL
CHANCE_CARD_POOL = list(_RULESET_CHANCE_CARD_POOL)
EVENT_CARD_POOL = list(_RULESET_EVENT_CARD_POOL)


def get_object_particle(word: str) -> str:
    if not word:
        return "를"

    last_char = word[-1]
    code = ord(last_char)

    # 한글 음절 범위: 가 ~ 힣
    if 0xAC00 <= code <= 0xD7A3:
        has_batchim = (code - 0xAC00) % 28 != 0
        return "을" if has_batchim else "를"

    # 한글이 아니면 보통 받침 없음으로 처리
    return "를"


def serialize_prompt(prompt: PendingPrompt | None) -> dict | None:
    if prompt is None:
        return None

    return {
        "id": prompt.prompt_id,
        "promptId": prompt.prompt_id,
        "type": prompt.type,
        "playerId": str(prompt.player_id),
        "title": prompt.title,
        "message": prompt.message,
        "timeoutSec": prompt.timeout_sec,
        "choices": [choice.to_json() for choice in prompt.choices],
        "payload": prompt.payload,
    }


def prompt_allowed_choices(prompt_type: str) -> tuple[str, ...]:
    return PROMPT_CHOICE_CANONICAL_MAP.get(prompt_type.upper(), ())


def normalize_prompt_choice(choice: str) -> str:
    return choice.strip().upper()


def default_prompt_choice(prompt: PendingPrompt) -> str:
    return prompt.default_choice or prompt.choices[0].value


def clear_prompt_patches(*, next_phase: str = PHASE_RESOLVING) -> list[dict]:
    return [
        op_set("pending_prompt", None),
        op_set("phase", next_phase),
    ]


def _format_money(amount: int) -> str:
    if amount >= 10000:
        r = f"{amount // 10000}억"
        if amount % 10000 > 0:
            r += f" {amount % 10000}만"
        return r
    return f"{amount}만"


def _get_build_stage_name(level: int) -> str:
    return BUILDING_STAGE_LABELS.get(level, "건설")


def get_player_total_assets(state: GameState, player_id: int) -> int:
    # 승자 판정용 총자산 계산.
    # 현금 + 현재 소유 중인 땅의 자산가치를 더한다.
    player = state.require_player(player_id)
    total_assets = player.balance

    for tile_id in player.owned_tiles:
        tile_state = state.tile(tile_id)
        if tile_state is None or tile_state.owner_id != player_id:
            continue
        total_assets += _get_property_asset_value(tile_id, tile_state.building_level)

    return total_assets


def build_winner_payload(state: GameState, player_id: int) -> dict[str, int | str]:
    player = state.require_player(player_id)
    return {
        "playerId": player.player_id,
        "nickname": player.nickname,
        "balance": player.balance,
        "assets": get_player_total_assets(state, player_id),
    }


def find_winner_by_assets(
    state: GameState,
    players: list | None = None,
) -> dict[str, int | str] | None:
    candidates = players if players is not None else list(state.players.values())
    if not candidates:
        return None

    winner = max(
        candidates,
        key=lambda player: (
            get_player_total_assets(state, player.player_id),
            player.balance,
            -player.turn_order,
        ),
    )
    return build_winner_payload(state, winner.player_id)


def _player_name(state: GameState, player_id: int) -> str:
    player = state.player(player_id)
    return player.nickname if player else f"Player {player_id}"


def _make_prompt(
    *,
    prompt_type: str,
    player_id: int,
    title: str,
    message: str,
    choices: list[PromptChoice],
    payload: dict,
    default_choice_value: str,
) -> PendingPrompt:
    # 화면에 보일 문구와 선택지, 숨은 payload를 한 번에 묶는다.
    return PendingPrompt(
        prompt_id=f"prompt-{uuid4().hex[:10]}",
        type=prompt_type,
        player_id=player_id,
        title=title,
        message=message,
        timeout_sec=PROMPT_TIMEOUT_SECONDS,
        choices=choices,
        payload=payload,
        default_choice=default_choice_value,
    )


def _owned_tile_patches(state: GameState, player_id: int, tile_id: int) -> list[dict]:
    player = state.require_player(player_id)
    if tile_id in player.owned_tiles:
        return []
    return [op_push(f"players.{player_id}.owned_tiles", tile_id)]


def _bankrupt_player_patches(state: GameState, player_id: int) -> list[dict]:
    player = state.require_player(player_id)
    patches = [
        op_set(f"players.{player_id}.balance", 0),
        op_set(f"players.{player_id}.player_state", PlayerState.BANKRUPT),
        op_set(f"players.{player_id}.state_duration", 0),
        op_set(f"players.{player_id}.consecutive_doubles", 0),
        op_set(f"players.{player_id}.extra_turn_effect_turns_remaining", 0),
        op_set(f"players.{player_id}.extra_turn_effect_active", False),
        op_set(f"players.{player_id}.owned_tiles", []),
        op_set(f"players.{player_id}.building_levels", {}),
    ]

    for owned_tile_id in player.owned_tiles:
        patches.append(op_set(f"tiles.{owned_tile_id}.owner_id", None))
        patches.append(op_set(f"tiles.{owned_tile_id}.building_level", 0))

    return patches


def _bankrupt_player_events(player_id: int) -> list[dict]:
    return [
        {
            "type": ServerEventType.PLAYER_STATE_CHANGED,
            "playerId": player_id,
            "playerState": PlayerState.BANKRUPT,
            "reason": "insufficient_funds",
        }
    ]


def _append_game_over_if_last_survivor(
    state: GameState,
    patches: list[dict],
    events: list[dict],
) -> None:
    preview_state = state.clone()
    apply_patches(preview_state, patches)
    active_players = preview_state.active_players()

    if len(active_players) > 1:
        return

    winner = active_players[0] if active_players else None
    winner_payload = (
        None
        if winner is None
        else build_winner_payload(preview_state, winner.player_id)
    )

    patches.extend(
        [
            op_set("status", "finished"),
            op_set("phase", PHASE_GAME_OVER),
            op_set("pending_prompt", None),
            op_set("winner_id", winner.player_id if winner is not None else None),
        ]
    )
    events.append(
        {
            "type": ServerEventType.GAME_OVER,
            "reason": "last_player_standing",
            "winner": winner_payload,
        }
    )


def _apply_money_delta(
    state: GameState,
    *,
    player_id: int,
    amount: int,
) -> tuple[list[dict], list[dict]]:
    # 돈이 늘거나 줄었을 때 잔액 변경과 파산 여부 확인을 같이 처리한다.
    player = state.require_player(player_id)
    next_balance = player.balance + amount
    if next_balance > 0:
        return [op_inc(f"players.{player_id}.balance", amount)], []

    patches = _bankrupt_player_patches(state, player_id)
    events = _bankrupt_player_events(player_id)
    _append_game_over_if_last_survivor(state, patches, events)
    return patches, events


def _get_global_toll_multiplier(state: GameState) -> float:
    if state.global_effects.toll_multiplier_turns_remaining <= 0:
        return 1.0
    return float(state.global_effects.toll_multiplier_value)


def _get_global_purchase_multiplier(state: GameState) -> float:
    if state.global_effects.price_multiplier_turns_remaining <= 0:
        return 1.0
    return float(state.global_effects.price_multiplier_value)


def _get_toll_amount(state: GameState, tile_id: int, building_level: int) -> int:
    tile_def = TILE_MAP[tile_id]
    normalized_level = max(0, min(building_level, len(tile_def.tolls) - 1))
    return _apply_price_multiplier(
        tile_def.tolls[normalized_level],
        _get_global_toll_multiplier(state),
    )


def _get_purchase_cost(state: GameState, tile_id: int) -> int:
    return _apply_price_multiplier(
        TILE_MAP[tile_id].price,
        _get_global_purchase_multiplier(state),
    )


def _get_build_cost(state: GameState, tile_id: int, building_level: int) -> int:
    tile_def = TILE_MAP[tile_id]
    normalized_level = max(0, min(building_level, len(tile_def.build_costs) - 1))
    return _apply_price_multiplier(
        tile_def.build_costs[normalized_level],
        _get_global_purchase_multiplier(state),
    )


def _get_sell_refund(tile_id: int, building_level: int) -> int:
    base_price = TILE_MAP[tile_id].price
    if building_level < 0 or base_price <= 0:
        return 0

    refund = int(base_price * SELL_PURCHASE_PRICE_REFUND_RATIO)
    for current_level in range(1, building_level + 1):
        refund += int(
            TILE_MAP[tile_id].build_costs[current_level - 1]
            * SELL_BUILD_COST_REFUND_RATIO
        )

    return refund


def _apply_price_multiplier(amount: int, multiplier: float) -> int:
    scaled = Decimal(amount) * Decimal(str(multiplier))
    return int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _get_property_asset_value(tile_id: int, building_level: int) -> int:
    tile_def = TILE_MAP[tile_id]
    if building_level < 0:
        return tile_def.price

    invested_build_cost = sum(tile_def.build_costs[:building_level])
    return tile_def.price + invested_build_cost


def _get_acquisition_cost(
    state: GameState,
    tile_id: int,
    building_level: int,
) -> int:
    del state
    return _apply_price_multiplier(
        _get_property_asset_value(tile_id, building_level),
        ACQUISITION_PRICE_MULTIPLIER,
    )


def _apply_property_acquisition(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    return apply_property_acquisition_action(
        state,
        player_id=player_id,
        tile_id=tile_id,
        context=PROPERTY_ACTION_CONTEXT,
    )


def _apply_chance_card(
    state: GameState,
    player_id: int,
    card: dict,
) -> tuple[list[dict], list[dict]]:
    card_effect = build_card_effect(str(card["type"]))
    return card_effect.apply(
        state=state,
        player_id=player_id,
        card=card,
        context=CARD_EFFECT_CONTEXT,
    )


def _append_landed_event(events: list[dict], *, player_id: int, tile_id: int) -> None:
    tile_def = TILE_MAP[tile_id]
    events.append(
        {
            "type": ServerEventType.LANDED,
            "playerId": player_id,
            "tile": {
                "tileId": tile_def.tile_id,
                "name": tile_def.name,
                "tileType": str(tile_def.tile_type),
                "tier": tile_def.tier,
                "price": tile_def.price,
            },
        }
    )


def _queue_follow_up_landing_prompt(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    patches: list[dict],
    events: list[dict],
) -> None:
    preview_state = state.clone()
    apply_patches(preview_state, patches)
    follow_up_events, follow_up_patches = resolve_landing(
        preview_state,
        player_id,
        tile_id,
    )
    events.extend(follow_up_events)
    patches.extend(follow_up_patches)


def _queue_follow_up_landing_resolution(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    patches: list[dict],
    events: list[dict],
    include_landed_event: bool,
) -> None:
    preview_state = state.clone()
    apply_patches(preview_state, patches)
    if include_landed_event:
        _append_landed_event(events, player_id=player_id, tile_id=tile_id)
    follow_up_events, follow_up_patches = resolve_landing(
        preview_state,
        player_id,
        tile_id,
    )
    events.extend(follow_up_events)
    patches.extend(follow_up_patches)


def _build_landing_context() -> LandingContext:
    # 타일 객체가 필요한 외부 함수들을 한 묶음으로 전달한다.
    def append_landed_event(
        events: list[dict],
        player_id: int,
        tile_id: int,
    ) -> None:
        _append_landed_event(events, player_id=player_id, tile_id=tile_id)

    def queue_follow_up_landing_resolution(
        state: GameState,
        player_id: int,
        tile_id: int,
        patches: list[dict],
        events: list[dict],
        include_landed_event: bool,
    ) -> None:
        _queue_follow_up_landing_resolution(
            state,
            player_id=player_id,
            tile_id=tile_id,
            patches=patches,
            events=events,
            include_landed_event=include_landed_event,
        )

    return LandingContext(
        phase_wait_prompt=PHASE_WAIT_PROMPT,
        phase_resolving=PHASE_RESOLVING,
        phase_game_over=PHASE_GAME_OVER,
        max_building_level=MAX_BUILDING_LEVEL,
        island_tile_id=ISLAND_TILE_ID,
        chance_cards=CHANCE_CARD_POOL,
        event_cards=EVENT_CARD_POOL,
        choose_random=lambda items: random.choice(items),
        make_prompt=_make_prompt,
        get_object_particle=get_object_particle,
        format_money=_format_money,
        get_build_stage_name=_get_build_stage_name,
        get_purchase_cost=_get_purchase_cost,
        get_build_cost=_get_build_cost,
        get_toll_amount=_get_toll_amount,
        get_acquisition_cost=_get_acquisition_cost,
        player_name=_player_name,
        apply_card=_apply_chance_card,
        append_landed_event=append_landed_event,
        queue_follow_up_landing_resolution=queue_follow_up_landing_resolution,
    )


LANDING_CONTEXT = _build_landing_context()


def _build_prompt_context() -> PromptContext:
    def queue_follow_up_landing_prompt(
        state: GameState,
        player_id: int,
        tile_id: int,
        patches: list[dict],
        events: list[dict],
    ) -> None:
        _queue_follow_up_landing_prompt(
            state,
            player_id=player_id,
            tile_id=tile_id,
            patches=patches,
            events=events,
        )

    def append_landed_event(
        events: list[dict],
        player_id: int,
        tile_id: int,
    ) -> None:
        _append_landed_event(events, player_id=player_id, tile_id=tile_id)

    return PromptContext(
        board_size=BOARD_SIZE,
        phase_wait_prompt=PHASE_WAIT_PROMPT,
        make_prompt=_make_prompt,
        format_money=_format_money,
        apply_purchase=lambda state, player_id, tile_id: _apply_purchase(
            state,
            player_id=player_id,
            tile_id=tile_id,
        ),
        apply_build=lambda state, player_id, tile_id: _apply_build(
            state,
            player_id=player_id,
            tile_id=tile_id,
        ),
        apply_toll_payment=lambda state, player_id, tile_id: _apply_toll_payment(
            state,
            player_id=player_id,
            tile_id=tile_id,
        ),
        apply_property_acquisition=lambda state, player_id, tile_id: (
            _apply_property_acquisition(
                state,
                player_id=player_id,
                tile_id=tile_id,
            )
        ),
        queue_follow_up_landing_prompt=queue_follow_up_landing_prompt,
        append_landed_event=append_landed_event,
        resolve_landing=resolve_landing,
    )


def _build_card_effect_context() -> CardEffectContext:
    return CardEffectContext(
        board_size=BOARD_SIZE,
        start_salary=START_SALARY,
        apply_money_delta=lambda state, player_id, amount: _apply_money_delta(
            state,
            player_id=player_id,
            amount=amount,
        ),
        choose_random=lambda items: random.choice(items),
    )


def _build_property_action_context() -> PropertyActionContext:
    return PropertyActionContext(
        max_building_level=MAX_BUILDING_LEVEL,
        get_sell_refund=_get_sell_refund,
        get_purchase_cost=_get_purchase_cost,
        get_build_cost=_get_build_cost,
        get_acquisition_cost=_get_acquisition_cost,
        get_toll_amount=_get_toll_amount,
        owned_tile_patches=_owned_tile_patches,
        bankrupt_player_patches=_bankrupt_player_patches,
        bankrupt_player_events=_bankrupt_player_events,
        append_game_over_if_last_survivor=_append_game_over_if_last_survivor,
    )


CARD_EFFECT_CONTEXT = _build_card_effect_context()
PROPERTY_ACTION_CONTEXT = _build_property_action_context()


def _apply_purchase(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    return apply_purchase_action(
        state,
        player_id=player_id,
        tile_id=tile_id,
        context=PROPERTY_ACTION_CONTEXT,
    )


def _apply_build(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    return apply_build_action(
        state,
        player_id=player_id,
        tile_id=tile_id,
        context=PROPERTY_ACTION_CONTEXT,
    )


def _ensure_turn_management_action_available(
    state: GameState,
    *,
    player_id: int,
    invalid_phase_message: str,
) -> None:
    if state.current_player_id != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="현재 턴이 아닙니다.")
    if state.status != "playing" or state.phase not in (
        PHASE_WAIT_ROLL,
        PHASE_RESOLVING,
    ):
        raise GameActionError(
            code="INVALID_PHASE",
            message=invalid_phase_message,
        )


def _apply_toll_payment(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    return apply_toll_payment_action(
        state,
        player_id=player_id,
        tile_id=tile_id,
        context=PROPERTY_ACTION_CONTEXT,
    )


def process_buy_property_action(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    if state.current_player_id != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="내 턴이 아닙니다.")
    if state.status != "playing" or state.phase != PHASE_WAIT_ROLL:
        raise GameActionError(
            code="INVALID_PHASE",
            message="지금은 구매 또는 건설을 할 수 없습니다.",
        )

    tile_state = state.tile(tile_id)
    if tile_state is None:
        raise GameActionError(
            code="INVALID_TILE", message="이 칸에서는 해당 행동을 할 수 없습니다."
        )

    current_tile_id = state.require_player(player_id).current_tile_id
    if tile_state.owner_id is None and tile_id == current_tile_id:
        return _apply_purchase(state, player_id=player_id, tile_id=tile_id)
    raise GameActionError(
        code="INVALID_PHASE",
        message="토지 구매와 건설은 도착 시 표시되는 프롬프트로만 진행할 수 있습니다.",
    )


def process_city_build_action(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    raise GameActionError(
        code="INVALID_PHASE",
        message="건설은 도착한 칸에서만 프롬프트로 진행할 수 있습니다.",
    )


def process_sell_property_action(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    building_level: int | None,
) -> tuple[list[dict], list[dict]]:
    _ensure_turn_management_action_available(
        state,
        player_id=player_id,
        invalid_phase_message="지금은 매각할 수 없습니다.",
    )

    return apply_sell_property_action(
        state,
        player_id=player_id,
        tile_id=tile_id,
        building_level=building_level,
        context=PROPERTY_ACTION_CONTEXT,
    )


def process_turn_sell_property_action(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    building_level: int | None,
) -> tuple[list[dict], list[dict]]:
    return process_sell_property_action(
        state,
        player_id=player_id,
        tile_id=tile_id,
        building_level=building_level,
    )


def resolve_landing(
    state: GameState,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    # 플레이어가 특정 칸에 도착했을 때의 규칙 진입점.
    tile_def = TILE_MAP[tile_id]
    tile_state = state.tile(tile_id)
    events: list[dict] = []
    patches: list[dict] = [op_set("phase", PHASE_RESOLVING)]
    tile_handler = build_tile_handler(tile_def)
    tile_handler.on_land(
        state=state,
        player_id=player_id,
        tile_state=tile_state,
        events=events,
        patches=patches,
        context=LANDING_CONTEXT,
    )
    return events, patches


PROMPT_CONTEXT = _build_prompt_context()


def process_prompt_response(
    state: GameState,
    *,
    player_id: int,
    prompt_id: str,
    choice: str,
    payload: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    # 사용자가 프롬프트에서 버튼을 눌렀을 때의 규칙 진입점.
    prompt = state.pending_prompt
    if prompt is None or prompt.prompt_id != prompt_id:
        raise GameActionError(
            code="PROMPT_NOT_FOUND",
            message="진행 중인 프롬프트를 찾을 수 없습니다.",
        )

    if state.phase != PHASE_WAIT_PROMPT:
        raise GameActionError(
            code="INVALID_PHASE",
            message="현재 단계에서는 프롬프트를 처리할 수 없습니다.",
        )

    if prompt.player_id != player_id:
        raise GameActionError(
            code="NOT_PROMPT_OWNER",
            message="해당 프롬프트의 응답 대상이 아닙니다.",
        )

    normalized_choice = normalize_prompt_choice(choice)
    if normalized_choice not in prompt_allowed_choices(prompt.type):
        raise GameActionError(
            code="INVALID_PROMPT_CHOICE",
            message="올바르지 않은 프롬프트 선택입니다.",
        )

    patches = clear_prompt_patches()
    events: list[dict] = []
    response_payload = payload if isinstance(payload, dict) else {}
    prompt_handler = build_prompt_handler(prompt.type)
    prompt_handler.handle(
        state=state,
        player_id=player_id,
        prompt=prompt,
        normalized_choice=normalized_choice,
        payload=response_payload,
        patches=patches,
        events=events,
        context=PROMPT_CONTEXT,
    )
    return events, patches
