from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeAlias

from app.game.enums import ServerEventType, TileType
from app.game.errors import GameActionError
from app.game.models import GameState
from app.game.patch import op_inc, op_remove, op_set

ActionResult: TypeAlias = tuple[list[dict], list[dict]]


@dataclass(frozen=True, slots=True)
class PropertyActionContext:
    max_building_level: int
    get_sell_refund: Callable[[int, int], int]
    get_acquisition_cost: Callable[[int, int], int]
    get_toll_amount: Callable[[int, int], int]
    owned_tile_patches: Callable[[GameState, int, int], list[dict]]
    bankrupt_player_patches: Callable[[GameState, int], list[dict]]
    bankrupt_player_events: Callable[[int], list[dict]]
    append_game_over_if_last_survivor: Callable[[GameState, list[dict], list[dict]], None]


def apply_property_acquisition(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    context: PropertyActionContext,
) -> ActionResult:
    tile_def = state.tile(tile_id)
    board_tile = None
    try:
        from app.game.board import TILE_MAP

        board_tile = TILE_MAP.get(tile_id)
    except Exception:
        board_tile = None
    if board_tile is None or tile_def is None or board_tile.tile_type != TileType.PROPERTY:
        raise GameActionError(code="INVALID_TILE", message="인수할 수 없는 타일입니다.")

    owner_id = tile_def.owner_id
    if owner_id is None or owner_id == player_id:
        raise GameActionError(code="INVALID_PHASE", message="인수 대상 땅이 없습니다.")

    player = state.require_player(player_id)
    acquisition_cost = context.get_acquisition_cost(tile_id, tile_def.building_level)
    if player.balance < acquisition_cost:
        raise GameActionError(code="INSUFFICIENT_FUNDS", message="인수 금액이 부족합니다.")

    patches = [
        op_inc(f"players.{player_id}.balance", -acquisition_cost),
        op_inc(f"players.{owner_id}.balance", acquisition_cost),
        op_set(f"tiles.{tile_id}.owner_id", player_id),
        op_remove(f"players.{owner_id}.owned_tiles", tile_id),
        op_remove(f"players.{owner_id}.building_levels", tile_id),
        op_set(f"players.{player_id}.building_levels.{tile_id}", tile_def.building_level),
    ]
    patches.extend(context.owned_tile_patches(state, player_id, tile_id))
    events = [
        {
            "type": ServerEventType.ACQUIRED_PROPERTY,
            "playerId": player_id,
            "fromPlayerId": owner_id,
            "toPlayerId": player_id,
            "tileId": tile_id,
            "amount": acquisition_cost,
            "buildingLevel": tile_def.building_level,
        }
    ]
    return events, patches


def apply_purchase(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    context: PropertyActionContext,
) -> ActionResult:
    tile_state = state.tile(tile_id)
    from app.game.board import TILE_MAP

    tile_def = TILE_MAP.get(tile_id)
    if tile_def is None or tile_state is None or tile_def.tile_type != TileType.PROPERTY:
        raise GameActionError(code="INVALID_TILE", message="구매할 수 없는 타일입니다.")
    if tile_state.owner_id is not None:
        raise GameActionError(code="INVALID_PHASE", message="이미 소유자가 있는 타일입니다.")

    player = state.require_player(player_id)
    if player.balance < tile_def.price:
        raise GameActionError(code="INSUFFICIENT_FUNDS", message="보유 금액이 부족합니다.")

    patches = [
        op_inc(f"players.{player_id}.balance", -tile_def.price),
        op_set(f"tiles.{tile_id}.owner_id", player_id),
        op_set(f"tiles.{tile_id}.building_level", 0),
        op_set(f"players.{player_id}.building_levels.{tile_id}", 0),
    ]
    patches.extend(context.owned_tile_patches(state, player_id, tile_id))
    events = [
        {
            "type": ServerEventType.BOUGHT_PROPERTY,
            "playerId": player_id,
            "tileId": tile_id,
            "amount": tile_def.price,
        }
    ]
    return events, patches


def apply_build(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    context: PropertyActionContext,
) -> ActionResult:
    tile_state = state.tile(tile_id)
    from app.game.board import TILE_MAP

    tile_def = TILE_MAP.get(tile_id)
    if tile_def is None or tile_state is None or tile_def.tile_type != TileType.PROPERTY:
        raise GameActionError(code="INVALID_TILE", message="건설할 수 없는 타일입니다.")
    if tile_state.owner_id != player_id:
        raise GameActionError(code="NOT_OWNER", message="본인 소유 타일이 아닙니다.")
    current_level = tile_state.building_level
    if current_level >= context.max_building_level:
        raise GameActionError(code="INVALID_PHASE", message="이미 최대 단계까지 건설된 타일입니다.")

    build_cost = tile_def.build_costs[current_level]
    player = state.require_player(player_id)
    if player.balance < build_cost:
        raise GameActionError(code="INSUFFICIENT_FUNDS", message="보유 금액이 부족합니다.")

    next_level = current_level + 1
    events = [
        {
            "type": ServerEventType.BOUGHT_PROPERTY,
            "playerId": player_id,
            "tileId": tile_id,
            "amount": build_cost,
            "buildingLevel": next_level,
        }
    ]
    patches = [
        op_inc(f"players.{player_id}.balance", -build_cost),
        op_set(f"tiles.{tile_id}.building_level", next_level),
        op_set(f"players.{player_id}.building_levels.{tile_id}", next_level),
    ]
    return events, patches


def apply_toll_payment(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    context: PropertyActionContext,
) -> ActionResult:
    tile_state = state.tile(tile_id)
    from app.game.board import TILE_MAP

    tile_def = TILE_MAP.get(tile_id)
    if tile_def is None or tile_state is None or tile_def.tile_type != TileType.PROPERTY:
        raise GameActionError(code="INVALID_TILE", message="통행료를 지불할 수 없는 타일입니다.")

    owner_id = tile_state.owner_id
    if owner_id is None or owner_id == player_id:
        raise GameActionError(code="INVALID_PHASE", message="통행료를 지불할 대상이 없습니다.")

    building_level = tile_state.building_level
    toll = context.get_toll_amount(tile_id, building_level)
    player = state.require_player(player_id)
    payable_amount = min(player.balance, toll)
    events = [
        {
            "type": ServerEventType.PAID_TOLL,
            "fromPlayerId": player_id,
            "toPlayerId": owner_id,
            "amount": payable_amount,
            "tileId": tile_id,
        }
    ]
    patches: list[dict] = []
    if payable_amount > 0:
        patches.append(op_inc(f"players.{owner_id}.balance", payable_amount))
    if player.balance >= toll:
        patches.append(op_inc(f"players.{player_id}.balance", -toll))
        return events, patches

    patches.extend(context.bankrupt_player_patches(state, player_id))
    events.extend(context.bankrupt_player_events(player_id))
    context.append_game_over_if_last_survivor(state, patches, events)
    return events, patches


def apply_sell_property(
    state: GameState,
    *,
    player_id: int,
    tile_id: int,
    building_level: int | None,
    context: PropertyActionContext,
) -> ActionResult:
    tile_state = state.tile(tile_id)
    from app.game.board import TILE_MAP

    tile_def = TILE_MAP.get(tile_id)
    if tile_def is None or tile_state is None or tile_def.tile_type != TileType.PROPERTY:
        raise GameActionError(code="INVALID_TILE", message="매각할 수 없는 타일입니다.")
    if tile_state.owner_id != player_id:
        raise GameActionError(code="NOT_OWNER", message="본인 소유 타일이 아닙니다.")

    current_level = tile_state.building_level
    requested_level = (
        current_level if building_level is None else max(0, min(current_level, building_level))
    )
    refund = context.get_sell_refund(tile_id, requested_level)
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
        patches.append(op_set(f"players.{player_id}.building_levels.{tile_id}", next_level))
    return events, patches
