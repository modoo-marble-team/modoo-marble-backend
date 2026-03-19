from __future__ import annotations

import random
from uuid import uuid4

from app.game.board import BOARD_SIZE, ISLAND_TILE_ID, START_SALARY, TILE_MAP, TileType
from app.game.enums import PlayerState, ServerEventType
from app.game.errors import GameActionError
from app.game.models import GameState, PendingPrompt, PromptChoice
from app.game.patch import op_inc, op_push, op_remove, op_set
from app.game.state import apply_patches

PHASE_WAIT_ROLL = "WAIT_ROLL"
PHASE_RESOLVING = "RESOLVING"
PHASE_WAIT_PROMPT = "WAIT_PROMPT"
PHASE_GAME_OVER = "GAME_OVER"

SELL_REFUND_RATE_TIER_1_3 = 0.5
SELL_REFUND_RATE_TIER_4_5 = 1.0
SELL_REFUND_MULTIPLIER_TIER_6 = 2

PROMPT_TIMEOUT_SECONDS = 30

PROMPT_CHOICE_CANONICAL_MAP: dict[str, tuple[str, ...]] = {
    "BUY_OR_SKIP": ("BUY", "SKIP"),
    "BUILD_OR_SKIP": ("BUILD", "SKIP"),
    "PAY_TOLL": ("PAY_TOLL",),
    "CONFIRM_ONLY": ("CONFIRM",),
    "TRAVEL_SELECT": ("CONFIRM", "SKIP"),
}

CHANCE_EFFECTS: dict[int, tuple[str, int]] = {
    3: ("GAIN_MONEY", 300),
    10: ("LOSE_MONEY", 150),
    27: ("GAIN_MONEY", 500),
}

EVENT_EFFECT_AMOUNT = 200

CHANCE_CARD_POOL: list[dict] = [
    {
        "type": "GAIN_MONEY",
        "amount": 300,
        "description": "보너스 300만원을 획득합니다.",
    },
    {
        "type": "GAIN_MONEY",
        "amount": 200,
        "description": "보너스 200만원을 획득합니다.",
    },
    {
        "type": "GAIN_MONEY",
        "amount": 500,
        "description": "보너스 500만원을 획득합니다.",
    },
    {"type": "LOSE_MONEY", "amount": 150, "description": "150만원을 지불합니다."},
    {"type": "LOSE_MONEY", "amount": 200, "description": "200만원을 지불합니다."},
    {"type": "LOSE_MONEY", "amount": 300, "description": "300만원을 지불합니다."},
    {"type": "MOVE_FORWARD", "amount": 3, "description": "앞으로 3칸 이동합니다."},
    {"type": "MOVE_FORWARD", "amount": 5, "description": "앞으로 5칸 이동합니다."},
    {"type": "MOVE_BACKWARD", "amount": 2, "description": "뒤로 2칸 이동합니다."},
    {"type": "MOVE_BACKWARD", "amount": 3, "description": "뒤로 3칸 이동합니다."},
    {
        "type": "STEAL_PROPERTY",
        "amount": 0,
        "description": "상대의 땅 하나를 가져옵니다.",
    },
    {"type": "GIVE_PROPERTY", "amount": 0, "description": "내 땅 하나를 넘겨줍니다."},
]

EVENT_CARD_POOL: list[dict] = [
    {"type": "GAIN_MONEY", "amount": 200, "description": "축하금 200만원을 받습니다."},
    {"type": "GAIN_MONEY", "amount": 100, "description": "지원금 100만원을 받습니다."},
    {"type": "LOSE_MONEY", "amount": 100, "description": "벌금 100만원을 냅니다."},
]


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


def _apply_money_delta(
    state: GameState,
    *,
    player_id: int,
    amount: int,
) -> tuple[list[dict], list[dict]]:
    player = state.require_player(player_id)
    next_balance = player.balance + amount
    if next_balance > 0:
        return [op_inc(f"players.{player_id}.balance", amount)], []

    return _bankrupt_player_patches(state, player_id), _bankrupt_player_events(
        player_id,
    )


def _get_toll_amount(tile_id: int, building_level: int) -> int:
    tile_def = TILE_MAP[tile_id]
    if building_level <= 0:
        return tile_def.price
    return tile_def.tolls[min(building_level, len(tile_def.tolls) - 1)]


def _get_sell_refund(tile_id: int, building_level: int) -> int:
    base_price = TILE_MAP[tile_id].price
    if building_level < 0 or base_price <= 0:
        return 0

    refund = base_price
    for current_level in range(1, building_level):
        if current_level in (1, 2, 3):
            refund += int(base_price * SELL_REFUND_RATE_TIER_1_3)
        elif current_level in (4, 5):
            refund += int(base_price * SELL_REFUND_RATE_TIER_4_5)
        elif current_level == 6:
            refund += base_price * SELL_REFUND_MULTIPLIER_TIER_6

    return refund


def _apply_chance_card(
    state: GameState,
    player_id: int,
    card: dict,
) -> tuple[list[dict], list[dict]]:
    chance_type = card["type"]
    amount = card.get("amount", 0)
    events: list[dict] = []
    patches: list[dict] = []

    if chance_type == "GAIN_MONEY":
        money_patches, money_events = _apply_money_delta(
            state,
            player_id=player_id,
            amount=amount,
        )
        patches.extend(money_patches)
        events.extend(money_events)

    elif chance_type == "LOSE_MONEY":
        money_patches, money_events = _apply_money_delta(
            state,
            player_id=player_id,
            amount=-amount,
        )
        patches.extend(money_patches)
        events.extend(money_events)

    elif chance_type == "MOVE_FORWARD":
        player = state.require_player(player_id)
        from_tile = player.current_tile_id
        to_tile = (from_tile + amount) % BOARD_SIZE
        passed_start = from_tile + amount >= BOARD_SIZE
        patches.append(op_set(f"players.{player_id}.current_tile_id", to_tile))
        events.append(
            {
                "type": ServerEventType.PLAYER_MOVED,
                "playerId": player_id,
                "fromTileId": from_tile,
                "toTileId": to_tile,
                "trigger": "chance",
                "passGo": passed_start,
            }
        )
        if passed_start:
            patches.append(op_inc(f"players.{player_id}.balance", START_SALARY))

    elif chance_type == "MOVE_BACKWARD":
        player = state.require_player(player_id)
        from_tile = player.current_tile_id
        to_tile = (from_tile - amount) % BOARD_SIZE
        patches.append(op_set(f"players.{player_id}.current_tile_id", to_tile))
        events.append(
            {
                "type": ServerEventType.PLAYER_MOVED,
                "playerId": player_id,
                "fromTileId": from_tile,
                "toTileId": to_tile,
                "trigger": "chance",
                "passGo": False,
            }
        )

    elif chance_type == "STEAL_PROPERTY":
        other_players = [
            (candidate_id, candidate)
            for candidate_id, candidate in state.players.items()
            if candidate_id != player_id
            and not candidate.is_bankrupt
            and candidate.owned_tiles
        ]
        if other_players:
            target_id, target_player = random.choice(other_players)
            stolen_tile_id = random.choice(target_player.owned_tiles)
            patches.extend(
                [
                    op_set(f"tiles.{stolen_tile_id}.owner_id", player_id),
                    op_set(f"tiles.{stolen_tile_id}.building_level", 0),
                    op_remove(f"players.{target_id}.owned_tiles", stolen_tile_id),
                    op_remove(f"players.{target_id}.building_levels", stolen_tile_id),
                    op_push(f"players.{player_id}.owned_tiles", stolen_tile_id),
                    op_set(f"players.{player_id}.building_levels.{stolen_tile_id}", 0),
                ]
            )
            events.append(
                {
                    "type": ServerEventType.CHANCE_RESOLVED,
                    "playerId": player_id,
                    "chance": {
                        "type": "STEAL_PROPERTY",
                        "fromPlayerId": target_id,
                        "tileId": stolen_tile_id,
                    },
                }
            )

    elif chance_type == "GIVE_PROPERTY":
        player = state.require_player(player_id)
        if player.owned_tiles:
            receivers = [
                candidate_id
                for candidate_id, candidate in state.players.items()
                if candidate_id != player_id and not candidate.is_bankrupt
            ]
            if receivers:
                given_tile_id = random.choice(player.owned_tiles)
                receiver_id = int(random.choice(receivers))
                patches.extend(
                    [
                        op_set(f"tiles.{given_tile_id}.owner_id", receiver_id),
                        op_set(f"tiles.{given_tile_id}.building_level", 0),
                        op_remove(f"players.{player_id}.owned_tiles", given_tile_id),
                        op_remove(
                            f"players.{player_id}.building_levels", given_tile_id
                        ),
                        op_push(f"players.{receiver_id}.owned_tiles", given_tile_id),
                        op_set(
                            f"players.{receiver_id}.building_levels.{given_tile_id}",
                            0,
                        ),
                    ]
                )
                events.append(
                    {
                        "type": ServerEventType.CHANCE_RESOLVED,
                        "playerId": player_id,
                        "chance": {
                            "type": "GIVE_PROPERTY",
                            "toPlayerId": receiver_id,
                            "tileId": given_tile_id,
                        },
                    }
                )

    return events, patches


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


def _apply_purchase(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP.get(tile_id)
    tile_state = state.tile(tile_id)
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(code="INVALID_TILE", message="구매할 수 없는 칸입니다.")

    if tile_state.owner_id is not None:
        raise GameActionError(
            code="INVALID_PHASE", message="이미 소유자가 있는 칸입니다."
        )

    player = state.require_player(player_id)
    if player.balance < tile_def.price:
        raise GameActionError(
            code="INSUFFICIENT_FUNDS", message="보유 금액이 부족합니다."
        )

    patches = [
        op_inc(f"players.{player_id}.balance", -tile_def.price),
        op_set(f"tiles.{tile_id}.owner_id", player_id),
        op_set(f"tiles.{tile_id}.building_level", 0),
        op_set(f"players.{player_id}.building_levels.{tile_id}", 0),
    ]
    patches.extend(_owned_tile_patches(state, player_id, tile_id))
    return [
        {
            "type": ServerEventType.BOUGHT_PROPERTY,
            "playerId": player_id,
            "tileId": tile_id,
            "amount": tile_def.price,
        }
    ], patches


def _apply_build(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP.get(tile_id)
    tile_state = state.tile(tile_id)
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(code="INVALID_TILE", message="건설할 수 없는 칸입니다.")

    if tile_state.owner_id != player_id:
        raise GameActionError(code="NOT_OWNER", message="내 소유의 칸이 아닙니다.")

    current_level = tile_state.building_level
    if current_level >= 7:
        raise GameActionError(
            code="INVALID_PHASE",
            message="이미 최대 단계까지 건설된 칸입니다.",
        )

    build_cost = tile_def.build_costs[current_level + 1]
    player = state.require_player(player_id)
    if player.balance < build_cost:
        raise GameActionError(
            code="INSUFFICIENT_FUNDS", message="보유 금액이 부족합니다."
        )

    next_level = current_level + 1
    return [
        {
            "type": ServerEventType.BOUGHT_PROPERTY,
            "playerId": player_id,
            "tileId": tile_id,
            "amount": build_cost,
            "buildingLevel": next_level,
        }
    ], [
        op_inc(f"players.{player_id}.balance", -build_cost),
        op_set(f"tiles.{tile_id}.building_level", next_level),
        op_set(f"players.{player_id}.building_levels.{tile_id}", next_level),
    ]


def _apply_toll_payment(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP.get(tile_id)
    tile_state = state.tile(tile_id)
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(
            code="INVALID_TILE",
            message="통행료를 지불할 수 없는 칸입니다.",
        )

    owner_id = tile_state.owner_id
    if owner_id is None or owner_id == player_id:
        raise GameActionError(
            code="INVALID_PHASE",
            message="통행료를 지불할 대상이 없습니다.",
        )

    building_level = tile_state.building_level
    toll = _get_toll_amount(tile_id, building_level)
    player = state.require_player(player_id)
    payable_amount = min(player.balance, toll)
    patches: list[dict] = []
    events: list[dict] = [
        {
            "type": ServerEventType.PAID_TOLL,
            "fromPlayerId": player_id,
            "toPlayerId": owner_id,
            "amount": payable_amount,
            "tileId": tile_id,
        }
    ]

    if payable_amount > 0:
        patches.append(op_inc(f"players.{owner_id}.balance", payable_amount))

    if player.balance >= toll:
        patches.append(op_inc(f"players.{player_id}.balance", -toll))
        return events, patches

    patches.extend(_bankrupt_player_patches(state, player_id))
    events.extend(_bankrupt_player_events(player_id))
    return events, patches


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

    if tile_state.owner_id is None:
        return _apply_purchase(state, player_id=player_id, tile_id=tile_id)
    if tile_state.owner_id == player_id:
        return _apply_build(state, player_id=player_id, tile_id=tile_id)
    raise GameActionError(
        code="INVALID_PHASE",
        message="다른 플레이어의 소유 칸입니다.",
    )


def process_sell_property_action(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    building_level: int | None,
) -> tuple[list[dict], list[dict]]:
    if state.current_player_id != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="내 턴이 아닙니다.")
    if state.status != "playing" or state.phase != PHASE_WAIT_ROLL:
        raise GameActionError(
            code="INVALID_PHASE", message="지금은 매각할 수 없습니다."
        )

    tile_def = TILE_MAP.get(tile_id)
    tile_state = state.tile(tile_id)
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(code="INVALID_TILE", message="매각할 수 없는 칸입니다.")

    if tile_state.owner_id != player_id:
        raise GameActionError(code="NOT_OWNER", message="내 소유의 칸이 아닙니다.")

    current_level = tile_state.building_level
    requested_level = (
        current_level
        if building_level is None
        else max(0, min(current_level, building_level))
    )
    refund = _get_sell_refund(tile_id, requested_level)
    next_level = max(requested_level - 1, 0)
    release_ownership = next_level <= 0

    patches = [
        op_inc(f"players.{player_id}.balance", refund),
        op_set(f"tiles.{tile_id}.building_level", next_level),
    ]
    events = [
        {
            "type": ServerEventType.SOLD_PROPERTY,
            "playerId": player_id,
            "tileId": tile_id,
            "amount": refund,
            "buildingLevel": next_level,
            "releaseOwnership": release_ownership,
        }
    ]

    if release_ownership:
        patches.extend(
            [
                op_set(f"tiles.{tile_id}.owner_id", None),
                op_remove(f"players.{player_id}.owned_tiles", tile_id),
                op_remove(f"players.{player_id}.building_levels", tile_id),
            ]
        )
    else:
        patches.append(
            op_set(f"players.{player_id}.building_levels.{tile_id}", next_level)
        )

    return events, patches


def resolve_landing(
    state: GameState,
    player_id: int,
    tile_id: int,
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP[tile_id]
    tile_state = state.tile(tile_id)
    events: list[dict] = []
    patches: list[dict] = [op_set("phase", PHASE_RESOLVING)]

    if tile_def.tile_type == TileType.PROPERTY and tile_state is not None:
        owner_id = tile_state.owner_id
        building_level = tile_state.building_level
        if owner_id is None:
            prompt = _make_prompt(
                prompt_type="BUY_OR_SKIP",
                player_id=player_id,
                title=f"{tile_def.name} 구매",
                message=f"{tile_def.name}을(를) {tile_def.price}만원에 구매하시겠습니까?",
                choices=[
                    PromptChoice(id="buy", label="구매", value="BUY"),
                    PromptChoice(id="skip", label="건너뛰기", value="SKIP"),
                ],
                payload={
                    "tileId": tile_id,
                    "tileName": tile_def.name,
                    "price": tile_def.price,
                    "buildingLevel": building_level,
                },
                default_choice_value="SKIP",
            )
            patches.extend(
                [
                    op_set("pending_prompt", prompt),
                    op_set("phase", PHASE_WAIT_PROMPT),
                ]
            )
            return events, patches

        if owner_id == player_id and building_level < 7:
            build_cost = tile_def.build_costs[building_level + 1]
            next_toll = _get_toll_amount(tile_id, building_level + 1)
            prompt = _make_prompt(
                prompt_type="BUILD_OR_SKIP",
                player_id=player_id,
                title=f"{tile_def.name} 건설",
                message=f"{tile_def.name}에 {build_cost}만원을 내고 건설하시겠습니까?",
                choices=[
                    PromptChoice(id="build", label="건설", value="BUILD"),
                    PromptChoice(id="skip", label="건너뛰기", value="SKIP"),
                ],
                payload={
                    "tileId": tile_id,
                    "tileName": tile_def.name,
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
                    op_set("phase", PHASE_WAIT_PROMPT),
                ]
            )
            return events, patches

        if owner_id != player_id:
            toll = _get_toll_amount(tile_id, building_level)
            prompt = _make_prompt(
                prompt_type="PAY_TOLL",
                player_id=player_id,
                title=f"{tile_def.name} 통행료",
                message=f"{_player_name(state, owner_id)}님에게 {toll}만원을 지불합니다.",
                choices=[PromptChoice(id="pay", label="확인", value="PAY_TOLL")],
                payload={
                    "tileId": tile_id,
                    "tileName": tile_def.name,
                    "ownerId": owner_id,
                    "ownerName": _player_name(state, owner_id),
                    "toll": toll,
                    "amount": toll,
                    "buildingLevel": building_level,
                },
                default_choice_value="PAY_TOLL",
            )
            patches.extend(
                [
                    op_set("pending_prompt", prompt),
                    op_set("phase", PHASE_WAIT_PROMPT),
                ]
            )
            return events, patches

    if tile_def.tile_type == TileType.MOVE_TO_ISLAND:
        patches.extend(
            [
                op_set(f"players.{player_id}.current_tile_id", ISLAND_TILE_ID),
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
                    "fromTileId": tile_id,
                    "toTileId": ISLAND_TILE_ID,
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
        return events, patches

    if tile_def.tile_type == TileType.TRAVEL:
        prompt = _make_prompt(
            prompt_type="TRAVEL_SELECT",
            player_id=player_id,
            title="여행",
            message="이동할 목적지를 선택해주세요.",
            choices=[
                PromptChoice(id="confirm", label="선택", value="CONFIRM"),
                PromptChoice(id="skip", label="건너뛰기", value="SKIP"),
            ],
            payload={"tileId": tile_id, "tileName": tile_def.name},
            default_choice_value="SKIP",
        )
        patches.extend(
            [
                op_set("pending_prompt", prompt),
                op_set("phase", PHASE_WAIT_PROMPT),
            ]
        )
        return events, patches

    if tile_def.tile_type == TileType.EVENT:
        card = random.choice(EVENT_CARD_POOL)
        card_events, card_patches = _apply_chance_card(state, player_id, card)
        patches.extend(card_patches)
        events.extend(card_events)
        events.append(
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "tileId": tile_id,
                "chance": {
                    "type": card["type"],
                    "power": card.get("amount", 0),
                    "description": card["description"],
                },
            }
        )
        return events, patches

    if tile_def.tile_type == TileType.CHANCE:
        card = random.choice(CHANCE_CARD_POOL)
        card_events, card_patches = _apply_chance_card(state, player_id, card)
        patches.extend(card_patches)
        events.extend(card_events)
        if not any(
            event.get("type") == ServerEventType.CHANCE_RESOLVED
            for event in card_events
        ):
            events.append(
                {
                    "type": ServerEventType.CHANCE_RESOLVED,
                    "playerId": player_id,
                    "tileId": tile_id,
                    "chance": {
                        "type": card["type"],
                        "power": card.get("amount", 0),
                        "description": card["description"],
                    },
                }
            )
        return events, patches

    if tile_def.tile_type == TileType.AI:
        events.append(
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "tileId": tile_id,
                "chance": {"type": "AI_SKIPPED", "power": 0},
            }
        )
        return events, patches

    return events, patches


def process_prompt_response(
    state: GameState,
    *,
    player_id: int,
    prompt_id: str,
    choice: str,
    payload: dict | None = None,
) -> tuple[list[dict], list[dict]]:
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
    tile_id = int(prompt.payload.get("tileId", -1))
    response_payload = payload if isinstance(payload, dict) else {}

    if prompt.type == "BUY_OR_SKIP" and normalized_choice == "BUY":
        action_events, action_patches = _apply_purchase(
            state,
            player_id=player_id,
            tile_id=tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)
    elif prompt.type == "BUILD_OR_SKIP" and normalized_choice == "BUILD":
        action_events, action_patches = _apply_build(
            state,
            player_id=player_id,
            tile_id=tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)
    elif prompt.type == "PAY_TOLL":
        action_events, action_patches = _apply_toll_payment(
            state,
            player_id=player_id,
            tile_id=tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)
    elif prompt.type == "TRAVEL_SELECT" and normalized_choice == "CONFIRM":
        raw_target_tile_id = response_payload.get("targetTileId")
        try:
            target_tile_id = int(raw_target_tile_id)
        except (TypeError, ValueError) as exc:
            raise GameActionError(
                code="INVALID_TILE",
                message="여행 목적지를 선택해주세요.",
            ) from exc

        if target_tile_id < 0 or target_tile_id >= BOARD_SIZE:
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
        _append_landed_event(events, player_id=player_id, tile_id=target_tile_id)

        preview_state = state.clone()
        apply_patches(preview_state, patches)
        landing_events, landing_patches = resolve_landing(
            preview_state,
            player_id,
            target_tile_id,
        )
        events.extend(landing_events)
        patches.extend(landing_patches)

    return events, patches
