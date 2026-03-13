from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from app.game.board import BOARD_SIZE, ISLAND_TILE_ID, TILE_MAP, TileType
from app.game.enums import PlayerState, ServerEventType
from app.game.errors import GameActionError
from app.game.schemas import GameState, PendingPrompt, PromptChoice
from app.game.state import apply_patches

PHASE_WAIT_ROLL = "WAIT_ROLL"
PHASE_RESOLVING = "RESOLVING"
PHASE_WAIT_PROMPT = "WAIT_PROMPT"
PHASE_GAME_OVER = "GAME_OVER"

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


def serialize_prompt(prompt: PendingPrompt | None) -> dict | None:
    if prompt is None:
        return None

    return {
        "id": prompt["prompt_id"],
        "promptId": prompt["prompt_id"],
        "type": prompt["type"],
        "playerId": str(prompt["player_id"]),
        "title": prompt["title"],
        "message": prompt["message"],
        "timeoutSec": prompt["timeout_sec"],
        "choices": prompt["choices"],
        "payload": prompt["payload"],
    }


def prompt_allowed_choices(prompt_type: str) -> tuple[str, ...]:
    return PROMPT_CHOICE_CANONICAL_MAP.get(prompt_type.upper(), ())


def normalize_prompt_choice(choice: str) -> str:
    return choice.strip().upper()


def default_prompt_choice(prompt: PendingPrompt) -> str:
    return prompt.get("default_choice", prompt["choices"][0]["value"])


def clear_prompt_patches(*, next_phase: str = PHASE_RESOLVING) -> list[dict]:
    return [
        {"op": "set", "path": "pending_prompt", "value": None},
        {"op": "set", "path": "phase", "value": next_phase},
    ]


def _player_name(state: GameState, player_id: int) -> str:
    player = state["players"].get(str(player_id))
    return player["nickname"] if player else f"Player {player_id}"


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
    player = state["players"][str(player_id)]
    if tile_id in player["ownedTiles"]:
        return []
    return [{"op": "push", "path": f"players.{player_id}.ownedTiles", "value": tile_id}]


def _bankrupt_player_patches(state: GameState, player_id: int) -> list[dict]:
    player = state["players"][str(player_id)]
    patches = [
        {"op": "set", "path": f"players.{player_id}.balance", "value": 0},
        {
            "op": "set",
            "path": f"players.{player_id}.playerState",
            "value": PlayerState.BANKRUPT,
        },
        {"op": "set", "path": f"players.{player_id}.stateDuration", "value": 0},
        {"op": "set", "path": f"players.{player_id}.consecutiveDoubles", "value": 0},
        {"op": "set", "path": f"players.{player_id}.ownedTiles", "value": []},
        {"op": "set", "path": f"players.{player_id}.buildingLevels", "value": {}},
    ]

    for tile_id in player["ownedTiles"]:
        patches.append({"op": "set", "path": f"tiles.{tile_id}.ownerId", "value": None})
        patches.append(
            {"op": "set", "path": f"tiles.{tile_id}.buildingLevel", "value": 0}
        )

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
    player = state["players"][str(player_id)]
    next_balance = player["balance"] + amount
    if next_balance > 0:
        return [
            {"op": "inc", "path": f"players.{player_id}.balance", "value": amount}
        ], []

    return _bankrupt_player_patches(state, player_id), _bankrupt_player_events(
        player_id
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
            refund += int(base_price * 0.5)
        elif current_level in (4, 5):
            refund += base_price
        elif current_level == 6:
            refund += base_price * 2

    return refund


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
    state: GameState, *, player_id: int, tile_id: int
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP.get(tile_id)
    tile_state = state["tiles"].get(str(tile_id))
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(code="INVALID_TILE", message="Cannot buy this tile.")

    if tile_state["ownerId"] is not None:
        raise GameActionError(code="INVALID_PHASE", message="Tile is already owned.")

    player = state["players"][str(player_id)]
    if player["balance"] < tile_def.price:
        raise GameActionError(code="INSUFFICIENT_FUNDS", message="Not enough funds.")

    patches = [
        {"op": "inc", "path": f"players.{player_id}.balance", "value": -tile_def.price},
        {"op": "set", "path": f"tiles.{tile_id}.ownerId", "value": player_id},
        {"op": "set", "path": f"tiles.{tile_id}.buildingLevel", "value": 0},
        {
            "op": "set",
            "path": f"players.{player_id}.buildingLevels.{tile_id}",
            "value": 0,
        },
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
    state: GameState, *, player_id: int, tile_id: int
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP.get(tile_id)
    tile_state = state["tiles"].get(str(tile_id))
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(code="INVALID_TILE", message="Cannot build on this tile.")

    if tile_state["ownerId"] != player_id:
        raise GameActionError(code="NOT_OWNER", message="You do not own this tile.")

    current_level = tile_state["buildingLevel"]
    if current_level >= 7:
        raise GameActionError(
            code="INVALID_PHASE", message="This tile is already maxed out."
        )

    build_cost = tile_def.build_costs[current_level + 1]
    player = state["players"][str(player_id)]
    if player["balance"] < build_cost:
        raise GameActionError(code="INSUFFICIENT_FUNDS", message="Not enough funds.")

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
        {"op": "inc", "path": f"players.{player_id}.balance", "value": -build_cost},
        {"op": "set", "path": f"tiles.{tile_id}.buildingLevel", "value": next_level},
        {
            "op": "set",
            "path": f"players.{player_id}.buildingLevels.{tile_id}",
            "value": next_level,
        },
    ]


def _apply_toll_payment(
    state: GameState, *, player_id: int, tile_id: int
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP.get(tile_id)
    tile_state = state["tiles"].get(str(tile_id))
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(
            code="INVALID_TILE", message="Cannot pay toll on this tile."
        )

    owner_id = tile_state["ownerId"]
    if owner_id is None or owner_id == player_id:
        raise GameActionError(
            code="INVALID_PHASE", message="No toll target is available."
        )

    building_level = tile_state["buildingLevel"]
    toll = _get_toll_amount(tile_id, building_level)
    player = state["players"][str(player_id)]
    payable_amount = min(player["balance"], toll)
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
        patches.append(
            {
                "op": "inc",
                "path": f"players.{owner_id}.balance",
                "value": payable_amount,
            }
        )

    if player["balance"] >= toll:
        patches.append(
            {"op": "inc", "path": f"players.{player_id}.balance", "value": -toll}
        )
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
    if state["current_player_id"] != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="It is not your turn.")
    if state["status"] != "playing" or state["phase"] != PHASE_WAIT_ROLL:
        raise GameActionError(
            code="INVALID_PHASE", message="Cannot buy or build right now."
        )

    tile_state = state["tiles"].get(str(tile_id))
    if tile_state is None:
        raise GameActionError(code="INVALID_TILE", message="Cannot act on this tile.")

    owner_id = tile_state["ownerId"]
    if owner_id is None:
        return _apply_purchase(state, player_id=player_id, tile_id=tile_id)
    if owner_id == player_id:
        return _apply_build(state, player_id=player_id, tile_id=tile_id)
    raise GameActionError(
        code="INVALID_PHASE", message="This tile belongs to another player."
    )


def process_sell_property_action(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    building_level: int | None,
) -> tuple[list[dict], list[dict]]:
    if state["current_player_id"] != player_id:
        raise GameActionError(code="NOT_YOUR_TURN", message="It is not your turn.")
    if state["status"] != "playing" or state["phase"] != PHASE_WAIT_ROLL:
        raise GameActionError(code="INVALID_PHASE", message="Cannot sell right now.")

    tile_def = TILE_MAP.get(tile_id)
    tile_state = state["tiles"].get(str(tile_id))
    if (
        tile_def is None
        or tile_state is None
        or tile_def.tile_type != TileType.PROPERTY
    ):
        raise GameActionError(code="INVALID_TILE", message="Cannot sell this tile.")

    if tile_state["ownerId"] != player_id:
        raise GameActionError(code="NOT_OWNER", message="You do not own this tile.")

    current_level = tile_state["buildingLevel"]
    requested_level = (
        current_level
        if building_level is None
        else max(0, min(current_level, building_level))
    )
    refund = _get_sell_refund(tile_id, requested_level)
    next_level = max(requested_level - 1, 0)
    release_ownership = next_level <= 0

    patches = [
        {"op": "inc", "path": f"players.{player_id}.balance", "value": refund},
        {"op": "set", "path": f"tiles.{tile_id}.buildingLevel", "value": next_level},
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
        patches.append({"op": "set", "path": f"tiles.{tile_id}.ownerId", "value": None})
        patches.append(
            {
                "op": "remove",
                "path": f"players.{player_id}.ownedTiles",
                "value": tile_id,
            }
        )
        patches.append(
            {
                "op": "remove",
                "path": f"players.{player_id}.buildingLevels",
                "value": str(tile_id),
            }
        )
    else:
        patches.append(
            {
                "op": "set",
                "path": f"players.{player_id}.buildingLevels.{tile_id}",
                "value": next_level,
            }
        )

    return events, patches


def resolve_landing(
    state: GameState, player_id: int, tile_id: int
) -> tuple[list[dict], list[dict]]:
    tile_def = TILE_MAP[tile_id]
    tile_state = state["tiles"].get(str(tile_id))
    events: list[dict] = []
    patches: list[dict] = [{"op": "set", "path": "phase", "value": PHASE_RESOLVING}]

    if tile_def.tile_type == TileType.PROPERTY and tile_state is not None:
        owner_id = tile_state["ownerId"]
        building_level = tile_state["buildingLevel"]
        if owner_id is None:
            prompt = _make_prompt(
                prompt_type="BUY_OR_SKIP",
                player_id=player_id,
                title=f"{tile_def.name} purchase",
                message=f"Buy {tile_def.name} for {tile_def.price}만원?",
                choices=[
                    {"id": "buy", "label": "구매", "value": "BUY"},
                    {"id": "skip", "label": "건너뛰기", "value": "SKIP"},
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
                    {"op": "set", "path": "pending_prompt", "value": prompt},
                    {"op": "set", "path": "phase", "value": PHASE_WAIT_PROMPT},
                ]
            )
            return events, patches

        if owner_id == player_id and building_level < 7:
            build_cost = tile_def.build_costs[building_level + 1]
            next_toll = _get_toll_amount(tile_id, building_level + 1)
            prompt = _make_prompt(
                prompt_type="BUILD_OR_SKIP",
                player_id=player_id,
                title=f"{tile_def.name} build",
                message=f"Build on {tile_def.name} for {build_cost}만원?",
                choices=[
                    {"id": "build", "label": "건설", "value": "BUILD"},
                    {"id": "skip", "label": "건너뛰기", "value": "SKIP"},
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
                    {"op": "set", "path": "pending_prompt", "value": prompt},
                    {"op": "set", "path": "phase", "value": PHASE_WAIT_PROMPT},
                ]
            )
            return events, patches

        if owner_id != player_id:
            toll = _get_toll_amount(tile_id, building_level)
            prompt = _make_prompt(
                prompt_type="PAY_TOLL",
                player_id=player_id,
                title=f"{tile_def.name} toll",
                message=f"Pay {_player_name(state, owner_id)} {toll}만원.",
                choices=[
                    {"id": "pay", "label": "확인", "value": "PAY_TOLL"},
                ],
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
                    {"op": "set", "path": "pending_prompt", "value": prompt},
                    {"op": "set", "path": "phase", "value": PHASE_WAIT_PROMPT},
                ]
            )
            return events, patches

    if tile_def.tile_type == TileType.MOVE_TO_ISLAND:
        patches.extend(
            [
                {
                    "op": "set",
                    "path": f"players.{player_id}.currentTileId",
                    "value": ISLAND_TILE_ID,
                },
                {
                    "op": "set",
                    "path": f"players.{player_id}.playerState",
                    "value": PlayerState.LOCKED,
                },
                {"op": "set", "path": f"players.{player_id}.stateDuration", "value": 3},
                {
                    "op": "set",
                    "path": f"players.{player_id}.consecutiveDoubles",
                    "value": 0,
                },
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
            title="국내여행",
            message="이동할 칸을 선택하세요.",
            choices=[
                {"id": "confirm", "label": "목적지 선택", "value": "CONFIRM"},
                {"id": "skip", "label": "건너뛰기", "value": "SKIP"},
            ],
            payload={
                "tileId": tile_id,
                "tileName": tile_def.name,
            },
            default_choice_value="SKIP",
        )
        patches.extend(
            [
                {"op": "set", "path": "pending_prompt", "value": prompt},
                {"op": "set", "path": "phase", "value": PHASE_WAIT_PROMPT},
            ]
        )
        return events, patches

    if tile_def.tile_type == TileType.EVENT:
        money_patches, money_events = _apply_money_delta(
            state,
            player_id=player_id,
            amount=EVENT_EFFECT_AMOUNT,
        )
        patches.extend(money_patches)
        events.extend(money_events)
        events.append(
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "tileId": tile_id,
                "chance": {
                    "type": "EVENT_BONUS",
                    "power": EVENT_EFFECT_AMOUNT,
                },
            }
        )
        return events, patches

    if tile_def.tile_type == TileType.CHANCE:
        chance_type, amount = CHANCE_EFFECTS.get(tile_id, ("GAIN_MONEY", 200))
        delta = amount if chance_type == "GAIN_MONEY" else -amount
        money_patches, money_events = _apply_money_delta(
            state,
            player_id=player_id,
            amount=delta,
        )
        patches.extend(money_patches)
        events.extend(money_events)
        events.append(
            {
                "type": ServerEventType.CHANCE_RESOLVED,
                "playerId": player_id,
                "tileId": tile_id,
                "chance": {
                    "type": chance_type,
                    "power": amount,
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
                "chance": {
                    "type": "AI_SKIPPED",
                    "power": 0,
                },
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
    prompt = state.get("pending_prompt")
    if prompt is None or prompt["prompt_id"] != prompt_id:
        raise GameActionError(
            code="PROMPT_NOT_FOUND", message="No active prompt found."
        )

    if state["phase"] != PHASE_WAIT_PROMPT:
        raise GameActionError(
            code="INVALID_PHASE",
            message="Prompt cannot be handled in the current phase.",
        )

    if prompt["player_id"] != player_id:
        raise GameActionError(
            code="NOT_PROMPT_OWNER", message="You do not own this prompt."
        )

    normalized_choice = normalize_prompt_choice(choice)
    if normalized_choice not in prompt_allowed_choices(prompt["type"]):
        raise GameActionError(
            code="INVALID_PROMPT_CHOICE", message="Invalid prompt choice."
        )

    patches = clear_prompt_patches()
    events: list[dict] = []
    tile_id = int(prompt["payload"].get("tileId", -1))
    response_payload = payload if isinstance(payload, dict) else {}

    if prompt["type"] == "BUY_OR_SKIP" and normalized_choice == "BUY":
        action_events, action_patches = _apply_purchase(
            state,
            player_id=player_id,
            tile_id=tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)
    elif prompt["type"] == "BUILD_OR_SKIP" and normalized_choice == "BUILD":
        action_events, action_patches = _apply_build(
            state,
            player_id=player_id,
            tile_id=tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)
    elif prompt["type"] == "PAY_TOLL":
        action_events, action_patches = _apply_toll_payment(
            state,
            player_id=player_id,
            tile_id=tile_id,
        )
        events.extend(action_events)
        patches.extend(action_patches)
    elif prompt["type"] == "TRAVEL_SELECT" and normalized_choice == "CONFIRM":
        target_tile_id = response_payload.get("targetTileId")
        if not isinstance(target_tile_id, int):
            raise GameActionError(
                code="INVALID_TILE", message="Travel destination is required."
            )
        if target_tile_id < 0 or target_tile_id >= BOARD_SIZE:
            raise GameActionError(
                code="INVALID_TILE", message="Travel destination is out of range."
            )

        current_tile_id = state["players"][str(player_id)]["currentTileId"]
        if target_tile_id == current_tile_id:
            raise GameActionError(
                code="INVALID_TILE", message="Choose a different destination."
            )

        patches.append(
            {
                "op": "set",
                "path": f"players.{player_id}.currentTileId",
                "value": target_tile_id,
            }
        )
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

        preview_state = deepcopy(state)
        apply_patches(preview_state, patches)
        landing_events, landing_patches = resolve_landing(
            preview_state, player_id, target_tile_id
        )
        events.extend(landing_events)
        patches.extend(landing_patches)

    return events, patches
