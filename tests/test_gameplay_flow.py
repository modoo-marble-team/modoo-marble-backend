from __future__ import annotations

from app.game.actions.end_turn import MAX_ROUNDS, process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.board import BOARD, TILE_MAP
from app.game.enums import PlayerState, ServerEventType, TileType
from app.game.errors import GameActionError
from app.game.game_rules import (
    ACQUISITION_PRICE_MULTIPLIER,
    SELL_PURCHASE_PRICE_REFUND_RATIO,
)
from app.game.models import GameState, PlayerGameState, TileGameState
from app.game.presentation import serialize_game_snapshot
from app.game.rules import (
    process_city_build_action,
    process_prompt_response,
    process_sell_property_action,
    resolve_landing,
)
from app.game.state import INITIAL_BALANCE, apply_patches


def make_state() -> GameState:
    return GameState(
        game_id="1",
        room_id="room-1",
        revision=0,
        turn=1,
        round=1,
        current_player_id=1,
        status="playing",
        phase="WAIT_ROLL",
        pending_prompt=None,
        winner_id=None,
        players={
            1: PlayerGameState(
                player_id=1,
                nickname="host",
                balance=INITIAL_BALANCE,
                current_tile_id=0,
                player_state=PlayerState.NORMAL,
                state_duration=0,
                consecutive_doubles=0,
                owned_tiles=[],
                building_levels={},
                turn_order=0,
            ),
            2: PlayerGameState(
                player_id=2,
                nickname="guest",
                balance=INITIAL_BALANCE,
                current_tile_id=0,
                player_state=PlayerState.NORMAL,
                state_duration=0,
                consecutive_doubles=0,
                owned_tiles=[],
                building_levels={},
                turn_order=1,
            ),
        },
        tiles={
            tile.tile_id: TileGameState(owner_id=None, building_level=0)
            for tile in BOARD
            if tile.tile_type == TileType.PROPERTY
        },
    )


def test_minimum_gameplay_turn_rotation(monkeypatch):
    state = make_state()
    dice_values = iter([1, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )
    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "GAIN_MONEY",
            "amount": 10000,
            "description": "test bonus",
        },
    )

    roll_events, roll_patches = process_roll_dice(state, 1)
    apply_patches(state, roll_patches)
    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)
    state.revision += 1

    snapshot = serialize_game_snapshot(state)

    assert [event["type"] for event in roll_events][:3] == [
        "DICE_ROLLED",
        "PLAYER_MOVED",
        "LANDED",
    ]
    assert end_events[0]["type"] == "TURN_ENDED"
    assert snapshot["players"][0]["currentTileId"] == 3
    assert snapshot["currentPlayerId"] == "2"
    assert snapshot["round"] == 1


def test_locked_player_is_released_after_two_failed_turns(monkeypatch):
    state = make_state()
    player = state.require_player(1)
    player.current_tile_id = 8
    player.player_state = PlayerState.LOCKED
    player.state_duration = 2
    dice_values = iter([1, 2, 2, 3, 3, 4])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    first_events, first_patches = process_roll_dice(state, 1)
    apply_patches(state, first_patches)

    assert state.phase == "RESOLVING"
    assert state.require_player(1).current_tile_id == 8
    assert state.require_player(1).player_state == PlayerState.LOCKED
    assert state.require_player(1).state_duration == 1
    assert not any(event["type"] == "PLAYER_MOVED" for event in first_events)
    assert not any(event["type"] == "PLAYER_STATE_CHANGED" for event in first_events)

    _end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)
    state.current_player_id = 1

    second_events, second_patches = process_roll_dice(state, 1)
    apply_patches(state, second_patches)

    assert state.require_player(1).player_state == PlayerState.NORMAL
    assert state.require_player(1).state_duration == 0
    assert state.require_player(1).current_tile_id == 8
    assert not any(event["type"] == "PLAYER_MOVED" for event in second_events)
    assert any(
        event["type"] == "PLAYER_STATE_CHANGED"
        and event["reason"] == "timeout_escape"
        and event["playerState"] == PlayerState.NORMAL
        for event in second_events
    )

    _end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)
    state.current_player_id = 1

    third_events, third_patches = process_roll_dice(state, 1)
    apply_patches(state, third_patches)

    assert state.require_player(1).player_state == PlayerState.NORMAL
    assert state.require_player(1).state_duration == 0
    assert state.require_player(1).current_tile_id == 15
    assert any(
        event["type"] == "PLAYER_MOVED"
        and event["fromTileId"] == 8
        and event["toTileId"] == 15
        for event in third_events
    )
    assert not any(
        event["type"] == "PLAYER_STATE_CHANGED" and event["reason"] == "double_escape"
        for event in third_events
    )


def test_locked_player_double_escapes_and_moves_in_same_turn(monkeypatch):
    state = make_state()
    player = state.require_player(1)
    player.current_tile_id = 8
    player.player_state = PlayerState.LOCKED
    player.state_duration = 0
    dice_values = iter([1, 1])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )
    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {"type": "GAIN_MONEY", "amount": 0, "description": ""},
    )

    events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    assert any(
        event["type"] == "PLAYER_STATE_CHANGED"
        and event["reason"] == "double_escape"
        and event["playerState"] == PlayerState.NORMAL
        for event in events
    )
    assert any(
        event["type"] == "PLAYER_MOVED"
        and event["fromTileId"] == 8
        and event["toTileId"] == 10
        for event in events
    )
    assert state.require_player(1).player_state == PlayerState.NORMAL
    assert state.require_player(1).state_duration == 0
    assert state.require_player(1).current_tile_id == 10


def test_consecutive_double_rolls_do_not_send_player_to_island(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 30
    state.require_player(1).consecutive_doubles = 2
    dice_values = iter([3, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    assert any(
        event["type"] == "PLAYER_MOVED"
        and event["trigger"] == "normal"
        and event["fromTileId"] == 30
        and event["toTileId"] == 4
        for event in events
    )
    assert any(event["type"] == "PASSED_START" for event in events)
    assert state.require_player(1).current_tile_id == 4
    assert state.require_player(1).player_state == PlayerState.NORMAL
    assert state.require_player(1).state_duration == 0
    assert state.require_player(1).consecutive_doubles == 3


def test_landing_on_island_locks_player_and_clears_double_bonus(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 6
    dice_values = iter([1, 1])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    assert any(
        event["type"] == "PLAYER_MOVED"
        and event["fromTileId"] == 6
        and event["toTileId"] == 8
        for event in events
    )
    assert any(
        event["type"] == "PLAYER_STATE_CHANGED"
        and event["reason"] == "landed_on_island"
        and event["playerState"] == PlayerState.LOCKED
        for event in events
    )
    assert state.require_player(1).current_tile_id == 8
    assert state.require_player(1).player_state == PlayerState.LOCKED
    assert state.require_player(1).state_duration == 2
    assert state.require_player(1).consecutive_doubles == 0

    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert end_events[0]["type"] == "TURN_ENDED"
    assert end_events[0]["nextPlayerId"] == 2
    assert state.current_player_id == 2


def test_property_landing_creates_buy_prompt_and_purchase(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "BUY_OR_SKIP"
    assert state.phase == "WAIT_PROMPT"
    assert events[-1]["type"] == "LANDED"

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt.prompt_id,
        choice="BUY",
    )
    apply_patches(state, prompt_patches)

    assert any(event["type"] == "BOUGHT_PROPERTY" for event in prompt_events)
    assert state.tile(4).owner_id == 1
    assert state.require_player(1).balance == INITIAL_BALANCE - tile.price
    assert state.require_player(1).owned_tiles == [4]
    assert state.pending_prompt is not None
    assert state.pending_prompt.type == "BUILD_OR_SKIP"
    assert state.phase == "WAIT_PROMPT"

    build_prompt = state.pending_prompt
    build_events, build_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=build_prompt.prompt_id,
        choice="SKIP",
    )
    apply_patches(state, build_patches)

    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert build_events == []
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state.current_player_id == 2
    assert state.phase == "WAIT_ROLL"


def test_property_purchase_can_chain_into_build_prompt(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    buy_prompt = state.pending_prompt
    assert buy_prompt is not None
    assert buy_prompt.type == "BUY_OR_SKIP"

    _buy_events, buy_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=buy_prompt.prompt_id,
        choice="BUY",
    )
    apply_patches(state, buy_patches)

    build_prompt = state.pending_prompt
    assert build_prompt is not None
    assert build_prompt.type == "BUILD_OR_SKIP"
    assert build_prompt.payload["buildCost"] == tile.build_costs[0]

    build_events, build_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=build_prompt.prompt_id,
        choice="BUILD",
    )
    apply_patches(state, build_patches)

    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert any(event["type"] == "BOUGHT_PROPERTY" for event in build_events)
    assert state.tile(4).owner_id == 1
    assert state.tile(4).building_level == 1
    assert state.require_player(1).building_levels == {4: 1}
    assert (
        state.require_player(1).balance
        == INITIAL_BALANCE - tile.price - tile.build_costs[0]
    )
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state.current_player_id == 2


def test_property_landing_skip_does_not_transfer_ownership(monkeypatch):
    state = make_state()
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "BUY_OR_SKIP"

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt.prompt_id,
        choice="SKIP",
    )
    apply_patches(state, prompt_patches)

    assert state.tile(4).owner_id is None
    assert state.require_player(1).balance == INITIAL_BALANCE
    assert not any(event["type"] == "BOUGHT_PROPERTY" for event in prompt_events)


def test_property_purchase_requires_balance_above_price(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    state.require_player(1).balance = tile.price
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "BUY_OR_SKIP"

    try:
        process_prompt_response(
            state,
            player_id=1,
            prompt_id=prompt.prompt_id,
            choice="BUY",
        )
    except GameActionError as exc:
        assert exc.code == "INSUFFICIENT_FUNDS"
    else:
        raise AssertionError("expected insufficient funds error")


def test_owned_property_landing_prompts_for_toll_before_acquisition(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    state.tile(4).owner_id = 1
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 0}
    state.current_player_id = 2
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, patches = process_roll_dice(state, 2)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "PAY_TOLL"
    assert any(event["type"] == "LANDED" for event in events)

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=prompt.prompt_id,
        choice="PAY_TOLL",
    )
    apply_patches(state, prompt_patches)

    assert any(event["type"] == "PAID_TOLL" for event in prompt_events)
    assert state.require_player(1).balance == INITIAL_BALANCE + tile.tolls[0]
    assert state.require_player(2).balance == INITIAL_BALANCE - tile.tolls[0]
    assert state.pending_prompt is not None
    assert state.pending_prompt.type == "ACQUISITION_OR_SKIP"
    assert state.phase == "WAIT_PROMPT"


def test_owned_property_landing_can_acquire_full_property_with_buildings(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    state.current_player_id = 2
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, patches = process_roll_dice(state, 2)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "PAY_TOLL"
    assert any(event["type"] == "LANDED" for event in events)

    toll_events, toll_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=prompt.prompt_id,
        choice="PAY_TOLL",
    )
    apply_patches(state, toll_patches)

    acquisition_prompt = state.pending_prompt
    assert acquisition_prompt is not None
    assert acquisition_prompt.type == "ACQUISITION_OR_SKIP"
    expected_acquisition_cost = int(
        (tile.price + tile.build_costs[0] + tile.build_costs[1])
        * ACQUISITION_PRICE_MULTIPLIER
    )
    assert acquisition_prompt.payload["acquisitionCost"] == expected_acquisition_cost
    assert acquisition_prompt.payload["buildingLevel"] == 2
    assert any(event["type"] == "PAID_TOLL" for event in toll_events)

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=acquisition_prompt.prompt_id,
        choice="ACQUIRE",
    )
    apply_patches(state, prompt_patches)

    assert any(event["type"] == "ACQUIRED_PROPERTY" for event in prompt_events)
    assert state.tile(4).owner_id == 2
    assert state.tile(4).building_level == 2
    assert state.require_player(1).balance == (
        INITIAL_BALANCE + tile.tolls[2] + expected_acquisition_cost
    )
    assert state.require_player(2).balance == (
        INITIAL_BALANCE - tile.tolls[2] - expected_acquisition_cost
    )
    assert state.require_player(1).owned_tiles == []
    assert state.require_player(1).building_levels == {}
    assert state.require_player(2).owned_tiles == [4]
    assert state.require_player(2).building_levels == {4: 2}


def test_owned_property_landing_skip_pays_toll_without_transfer(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    state.current_player_id = 2
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 2)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "PAY_TOLL"

    toll_events, toll_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=prompt.prompt_id,
        choice="PAY_TOLL",
    )
    apply_patches(state, toll_patches)

    acquisition_prompt = state.pending_prompt
    assert acquisition_prompt is not None
    assert acquisition_prompt.type == "ACQUISITION_OR_SKIP"

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=acquisition_prompt.prompt_id,
        choice="SKIP",
    )
    apply_patches(state, prompt_patches)

    assert any(event["type"] == "PAID_TOLL" for event in toll_events)
    assert prompt_events == []
    assert not any(event["type"] == "ACQUIRED_PROPERTY" for event in prompt_events)
    assert state.tile(4).owner_id == 1
    assert state.require_player(1).balance == INITIAL_BALANCE + tile.tolls[2]
    assert state.require_player(2).balance == INITIAL_BALANCE - tile.tolls[2]


def test_property_acquisition_requires_enough_balance(monkeypatch):
    state = make_state()
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    state.require_player(2).balance = 70000
    state.current_player_id = 2
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 2)
    apply_patches(state, patches)

    toll_prompt = state.pending_prompt
    assert toll_prompt is not None

    toll_events, toll_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=toll_prompt.prompt_id,
        choice="PAY_TOLL",
    )
    apply_patches(state, toll_patches)
    assert any(event["type"] == "PAID_TOLL" for event in toll_events)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "ACQUISITION_OR_SKIP"

    try:
        process_prompt_response(
            state,
            player_id=2,
            prompt_id=prompt.prompt_id,
            choice="ACQUIRE",
        )
    except GameActionError as exc:
        assert exc.code == "INSUFFICIENT_FUNDS"
    else:
        raise AssertionError("expected insufficient funds error")


def test_bankruptcy_during_toll_payment_ends_game_immediately(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    state.tile(4).owner_id = 1
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 0}
    state.current_player_id = 2
    state.require_player(2).balance = tile.tolls[0] - 10
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 2)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "PAY_TOLL"

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=prompt.prompt_id,
        choice="PAY_TOLL",
    )
    apply_patches(state, prompt_patches)

    assert any(event["type"] == "PLAYER_STATE_CHANGED" for event in prompt_events)
    game_over_events = [
        event for event in prompt_events if event["type"] == ServerEventType.GAME_OVER
    ]
    assert len(game_over_events) == 1
    assert game_over_events[0]["reason"] == "last_player_standing"
    assert state.status == "finished"
    assert state.phase == "GAME_OVER"
    assert state.winner_id == 1
    assert state.require_player(2).player_state == PlayerState.BANKRUPT


def test_exact_toll_payment_also_causes_bankruptcy(monkeypatch):
    state = make_state()
    tile = TILE_MAP[4]
    state.tile(4).owner_id = 1
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 0}
    state.current_player_id = 2
    state.require_player(2).balance = tile.tolls[0]
    dice_values = iter([1, 3])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 2)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "PAY_TOLL"

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=prompt.prompt_id,
        choice="PAY_TOLL",
    )
    apply_patches(state, prompt_patches)

    assert any(event["type"] == "PLAYER_STATE_CHANGED" for event in prompt_events)
    assert state.require_player(2).player_state == PlayerState.BANKRUPT
    assert state.require_player(2).balance == 0


def test_sell_property_action_refunds_money_and_releases_tile():
    state = make_state()
    tile = TILE_MAP[4]
    state.tile(4).owner_id = 1
    state.require_player(1).owned_tiles = [4]

    events, patches = process_sell_property_action(
        state,
        player_id=1,
        tile_id=4,
        building_level=0,
    )
    apply_patches(state, patches)

    assert any(event["type"] == "SOLD_PROPERTY" for event in events)
    assert state.require_player(1).balance == INITIAL_BALANCE + int(
        tile.price * SELL_PURCHASE_PRICE_REFUND_RATIO
    )
    assert state.tile(4).owner_id is None
    assert state.require_player(1).owned_tiles == []


def test_city_build_action_is_rejected_outside_landing_prompt():
    state = make_state()
    state.tile(4).owner_id = 1
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 0}

    try:
        process_city_build_action(
            state,
            player_id=1,
            tile_id=4,
        )
    except GameActionError as exc:
        assert exc.code == "INVALID_PHASE"
    else:
        raise AssertionError("CITY_BUILD should be rejected outside landing prompt")

    assert state.require_player(1).balance == INITIAL_BALANCE
    assert state.tile(4).building_level == 0
    assert state.require_player(1).building_levels == {4: 0}


def test_travel_prompt_moves_to_selected_tile_and_chains_into_tile_prompt():
    state = make_state()
    state.require_player(1).current_tile_id = 16

    travel_events, travel_patches = resolve_landing(state, 1, 16)
    apply_patches(state, travel_patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "TRAVEL_SELECT"
    assert travel_events == []

    response_events, response_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt.prompt_id,
        choice="CONFIRM",
        payload={"targetTileId": 4},
    )
    apply_patches(state, response_patches)

    assert any(event["type"] == "PLAYER_MOVED" for event in response_events)
    assert state.require_player(1).current_tile_id == 4
    assert state.pending_prompt is not None
    assert state.pending_prompt.type == "BUY_OR_SKIP"
    assert state.phase == "WAIT_PROMPT"


def test_travel_past_start_does_not_grant_salary():
    state = make_state()
    player = state.require_player(1)
    player.current_tile_id = 16
    balance_before = player.balance

    travel_events, travel_patches = resolve_landing(state, 1, 16)
    apply_patches(state, travel_patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "TRAVEL_SELECT"
    assert travel_events == []

    response_events, response_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt.prompt_id,
        choice="CONFIRM",
        payload={"targetTileId": 4},
    )
    apply_patches(state, response_patches)

    assert any(
        event["type"] == "PLAYER_MOVED"
        and event["trigger"] == "travel"
        and event["fromTileId"] == 16
        and event["toTileId"] == 4
        for event in response_events
    )
    assert not any(
        event["type"] == "PLAYER_MOVED" and event.get("passGo") is True
        for event in response_events
    )
    assert state.require_player(1).balance == balance_before


def test_bankrupt_player_is_skipped_in_turn_order(monkeypatch):
    state = make_state()
    state.players[3] = PlayerGameState(
        player_id=3,
        nickname="third",
        balance=INITIAL_BALANCE,
        current_tile_id=0,
        player_state=PlayerState.NORMAL,
        state_duration=0,
        consecutive_doubles=0,
        owned_tiles=[],
        building_levels={},
        turn_order=2,
    )
    state.require_player(2).player_state = PlayerState.BANKRUPT

    dice_values = iter([1, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )
    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {"type": "GAIN_MONEY", "amount": 0, "description": ""},
    )

    roll_events, roll_patches = process_roll_dice(state, 1)
    apply_patches(state, roll_patches)

    if state.pending_prompt:
        prompt = state.pending_prompt
        _, prompt_patches = process_prompt_response(
            state,
            player_id=1,
            prompt_id=prompt.prompt_id,
            choice="SKIP",
        )
        apply_patches(state, prompt_patches)

    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert roll_events[0]["type"] == "DICE_ROLLED"
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state.current_player_id == 3
    assert end_events[0]["nextPlayerId"] == 3


def test_last_player_standing_triggers_game_over():
    state = make_state()
    state.require_player(2).player_state = PlayerState.BANKRUPT
    state.phase = "RESOLVING"

    events, patches = process_end_turn(state, 1)
    apply_patches(state, patches)

    game_over_events = [
        event for event in events if event["type"] == ServerEventType.GAME_OVER
    ]
    assert len(game_over_events) == 1
    assert game_over_events[0]["reason"] == "last_player_standing"
    assert game_over_events[0]["winner"]["assets"] == INITIAL_BALANCE
    assert game_over_events[0]["rankings"][0]["playerId"] == 1
    assert game_over_events[0]["rankings"][0]["isWinner"] is True
    assert game_over_events[0]["rankings"][1]["playerId"] == 2
    assert state.status == "finished"


def test_max_rounds_uses_total_assets_for_winner():
    state = make_state()
    tile = TILE_MAP[4]
    state.round = MAX_ROUNDS
    state.current_player_id = 2
    state.phase = "RESOLVING"
    state.require_player(1).balance = 300000
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    player_one_assets = 300000 + tile.price + tile.build_costs[0] + tile.build_costs[1]
    state.require_player(2).balance = player_one_assets - 1

    events, patches = process_end_turn(state, 2)
    apply_patches(state, patches)

    game_over_events = [
        event for event in events if event["type"] == ServerEventType.GAME_OVER
    ]
    assert len(game_over_events) == 1
    assert game_over_events[0]["reason"] == "max_rounds"
    assert game_over_events[0]["winner"]["playerId"] == 1
    assert game_over_events[0]["winner"]["assets"] == player_one_assets
    assert [item["playerId"] for item in game_over_events[0]["rankings"]] == [1, 2]
    assert game_over_events[0]["rankings"][0]["finalAssets"] == player_one_assets
    assert state.winner_id == 1
    assert state.status == "finished"


def test_consecutive_doubles_reset_after_turn_end(monkeypatch):
    state = make_state()
    state.require_player(1).consecutive_doubles = 1
    dice_values = iter([1, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )
    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {"type": "GAIN_MONEY", "amount": 0, "description": ""},
    )

    roll_events, roll_patches = process_roll_dice(state, 1)
    apply_patches(state, roll_patches)
    if state.pending_prompt is not None:
        prompt = state.pending_prompt
        _, prompt_patches = process_prompt_response(
            state,
            player_id=1,
            prompt_id=prompt.prompt_id,
            choice="SKIP",
        )
        apply_patches(state, prompt_patches)
    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert roll_events[0]["type"] == "DICE_ROLLED"
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state.require_player(1).consecutive_doubles == 0


def test_double_roll_grants_bonus_turn_after_end_turn(monkeypatch):
    state = make_state()
    dice_values = iter([2, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _roll_events, roll_patches = process_roll_dice(state, 1)
    apply_patches(state, roll_patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "BUY_OR_SKIP"

    _prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt.prompt_id,
        choice="SKIP",
    )
    apply_patches(state, prompt_patches)

    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert end_events[0]["type"] == "TURN_ENDED"
    assert end_events[0]["nextPlayerId"] == 1
    assert end_events[0]["bonusTurn"] is True
    assert state.current_player_id == 1
    assert state.phase == "WAIT_ROLL"
    assert state.turn == 2
    assert state.round == 1
    assert state.require_player(1).consecutive_doubles == 1


def test_end_turn_requires_roll_completion():
    state = make_state()

    try:
        process_end_turn(state, 1)
    except GameActionError as exc:
        assert exc.code == "INVALID_PHASE"
        assert exc.message == "주사위를 먼저 굴려야 합니다."
    else:
        raise AssertionError("process_end_turn should fail before rolling")


def test_chance_card_gain_money_increases_balance(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "GAIN_MONEY",
            "amount": 30000,
            "description": "jackpot",
        },
    )

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    assert state.require_player(1).balance == INITIAL_BALANCE + 30000
    assert any(event["type"] == "CHANCE_RESOLVED" for event in events)


def test_chance_card_description_template_replaces_value_alias(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "GAIN_MONEY",
            "amount": 30000,
            "description": "보상 $value$ 지급",
        },
    )

    events, _patches = resolve_landing(state, 1, 3)

    chance_event = next(event for event in events if event["type"] == "CHANCE_RESOLVED")
    assert chance_event["chance"]["description"] == "보상 30000 지급"


def test_chance_card_lose_money_decreases_balance(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "LOSE_MONEY",
            "amount": 15000,
            "description": "fine",
        },
    )

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    assert state.require_player(1).balance == INITIAL_BALANCE - 15000
    assert any(event["type"] == "CHANCE_RESOLVED" for event in events)


def test_toll_multiplier_card_applies_global_toll_boost_and_ticks_down(monkeypatch):
    state = make_state()
    state.require_player(2).current_tile_id = 3
    state.tile(1).owner_id = 1

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "TOLL_MULTIPLIER",
            "effect": "FESTIVAL",
            "duration": 3,
            "multiplier": 2,
            "description": "olympics",
        },
    )

    events, patches = resolve_landing(state, 2, 3)
    apply_patches(state, patches)

    assert state.global_effects.toll_multiplier_turns_remaining == 3
    assert state.global_effects.toll_multiplier_value == 2
    chance_event = next(event for event in events if event["type"] == "CHANCE_RESOLVED")
    assert chance_event["chance"]["type"] == "TOLL_MULTIPLIER"
    assert chance_event["chance"]["effect"] == "FESTIVAL"
    assert chance_event["chance"]["duration"] == 3
    assert chance_event["chance"]["multiplier"] == 2

    toll_events, toll_patches = resolve_landing(state, 2, 1)
    apply_patches(state, toll_patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.payload["toll"] == TILE_MAP[1].tolls[0] * 2
    assert not toll_events

    state.current_player_id = 2
    state.phase = "RESOLVING"
    end_events, end_patches = process_end_turn(state, 2)
    apply_patches(state, end_patches)

    assert end_events[0]["type"] == "TURN_ENDED"
    assert state.global_effects.toll_multiplier_turns_remaining == 2


def test_toll_multiplier_card_overwrites_existing_global_effect(monkeypatch):
    state = make_state()
    state.require_player(2).current_tile_id = 3
    state.global_effects.toll_multiplier_turns_remaining = 2
    state.global_effects.toll_multiplier_value = 2

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "TOLL_MULTIPLIER",
            "duration": 3,
            "multiplier": 0.5,
            "description": "pandemic",
        },
    )

    _events, patches = resolve_landing(state, 2, 3)
    apply_patches(state, patches)

    assert state.global_effects.toll_multiplier_turns_remaining == 3
    assert state.global_effects.toll_multiplier_value == 0.5


def test_toll_multiplier_card_emits_effect_and_duration_for_pandemic(monkeypatch):
    state = make_state()
    state.require_player(2).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "TOLL_MULTIPLIER",
            "effect": "PANDEMIC",
            "duration": 3,
            "multiplier": 0.5,
            "description": "pandemic",
        },
    )

    events, _patches = resolve_landing(state, 2, 3)

    chance_event = next(event for event in events if event["type"] == "CHANCE_RESOLVED")
    assert chance_event["chance"]["effect"] == "PANDEMIC"
    assert chance_event["chance"]["duration"] == 3
    assert chance_event["chance"]["multiplier"] == 0.5


def test_toll_multiplier_can_reduce_toll_prompt_amount():
    state = make_state()
    state.global_effects.toll_multiplier_turns_remaining = 3
    state.global_effects.toll_multiplier_value = 0.5
    state.tile(1).owner_id = 1

    _events, patches = resolve_landing(state, 2, 1)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.payload["toll"] == TILE_MAP[1].tolls[0] // 2


def test_inflation_increases_purchase_and_build_costs():
    state = make_state()
    state.global_effects.price_multiplier_turns_remaining = 3
    state.global_effects.price_multiplier_value = 1.5

    _events, patches = resolve_landing(state, 1, 1)
    apply_patches(state, patches)

    prompt = state.pending_prompt
    assert prompt is not None
    assert prompt.type == "BUY_OR_SKIP"
    assert prompt.payload["price"] == 45000

    _prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt.prompt_id,
        choice="BUY",
    )
    apply_patches(state, prompt_patches)

    assert state.require_player(1).balance == INITIAL_BALANCE - 45000

    build_prompt = state.pending_prompt
    assert build_prompt is not None
    assert build_prompt.type == "BUILD_OR_SKIP"
    assert build_prompt.payload["buildCost"] == 30000

    _build_events, build_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=build_prompt.prompt_id,
        choice="BUILD",
    )
    apply_patches(state, build_patches)

    assert state.require_player(1).balance == INITIAL_BALANCE - 75000


def test_extra_turn_card_grants_bonus_turn_after_double_chain(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "EXTRA_TURN",
            "duration": 2,
            "description": "traffic",
        },
    )

    _events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    assert state.require_player(1).extra_turn_effect_turns_remaining == 2

    state.phase = "RESOLVING"
    state.require_player(1).consecutive_doubles = 1
    first_end_events, first_end_patches = process_end_turn(state, 1)
    apply_patches(state, first_end_patches)

    assert first_end_events[0]["reason"] == "double_roll"
    assert state.require_player(1).extra_turn_effect_turns_remaining == 2
    assert state.require_player(1).extra_turn_effect_active is False

    state.phase = "RESOLVING"
    state.require_player(1).consecutive_doubles = 0
    second_end_events, second_end_patches = process_end_turn(state, 1)
    apply_patches(state, second_end_patches)

    assert second_end_events[0]["reason"] == "extra_turn_effect"
    assert second_end_events[0]["nextPlayerId"] == 1
    assert state.require_player(1).extra_turn_effect_turns_remaining == 1
    assert state.require_player(1).extra_turn_effect_active is True

    state.phase = "RESOLVING"
    third_end_events, third_end_patches = process_end_turn(state, 1)
    apply_patches(state, third_end_patches)

    assert third_end_events[0]["nextPlayerId"] == 2
    assert state.require_player(1).extra_turn_effect_turns_remaining == 1
    assert state.require_player(1).extra_turn_effect_active is False


def test_steal_property_card_selects_from_all_opponent_owned_tiles(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3
    state.players[3] = PlayerGameState(
        player_id=3,
        nickname="guest-2",
        balance=INITIAL_BALANCE,
        current_tile_id=0,
        player_state=PlayerState.NORMAL,
        state_duration=0,
        consecutive_doubles=0,
        owned_tiles=[6],
        building_levels={6: 3},
        turn_order=2,
    )

    state.require_player(2).owned_tiles = [4, 5]
    state.require_player(2).building_levels = {4: 1, 5: 2}
    state.tiles[4].owner_id = 2
    state.tiles[4].building_level = 1
    state.tiles[5].owner_id = 2
    state.tiles[5].building_level = 2
    state.tiles[6].owner_id = 3
    state.tiles[6].building_level = 3

    def choose(pool):
        first = pool[0]
        if isinstance(first, dict):
            return {
                "type": "STEAL_PROPERTY",
                "amount": 0,
                "description": "$player$의 $property$$suffix$ 점유했습니다.",
            }
        return pool[1] if len(pool) > 1 else pool[0]

    monkeypatch.setattr("app.game.rules.random.choice", choose)

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    chance_event = next(event for event in events if event["type"] == "CHANCE_RESOLVED")

    assert chance_event["tileId"] == 3
    assert chance_event["chance"]["tileId"] == 5
    assert chance_event["chance"]["fromPlayerId"] == 2
    assert chance_event["chance"]["description"] == "guest의 태백을 점유했습니다."
    assert state.require_player(1).owned_tiles == [5]
    assert state.require_player(2).owned_tiles == [4]
    assert state.require_player(3).owned_tiles == [6]
    assert state.tiles[5].owner_id == 1
    assert state.tiles[5].building_level == 0


def test_steal_property_card_uses_failed_description_when_no_target_exists(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "STEAL_PROPERTY",
            "amount": 0,
            "description": "$player$의 $property$$suffix$ 점유했습니다.",
            "failed_description": "땅 훔치기 실패",
        },
    )

    events, _patches = resolve_landing(state, 1, 3)

    chance_event = next(event for event in events if event["type"] == "CHANCE_RESOLVED")
    assert chance_event["tileId"] == 3
    assert chance_event["chance"]["description"] == "땅 훔치기 실패"


def test_chance_move_card_resolves_before_move_and_chains_into_property_prompt(
    monkeypatch,
):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "MOVE_BACKWARD",
            "amount": 2,
            "description": "뒤로 2칸 이동합니다.",
        },
    )

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    chance_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == "CHANCE_RESOLVED"
    )
    move_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == "PLAYER_MOVED" and event.get("trigger") == "chance"
    )

    assert chance_index < move_index
    assert any(
        event["type"] == "LANDED" and event["tile"]["tileId"] == 1 for event in events
    )
    assert state.require_player(1).current_tile_id == 1
    assert state.pending_prompt is not None
    assert state.pending_prompt.type == "BUY_OR_SKIP"


def test_chance_move_card_can_chain_into_move_to_island_resolution(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 27

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "MOVE_BACKWARD",
            "amount": 3,
            "description": "뒤로 3칸 이동합니다.",
        },
    )

    events, patches = resolve_landing(state, 1, 27)
    apply_patches(state, patches)

    assert any(
        event["type"] == "CHANCE_RESOLVED" and event["tileId"] == 27 for event in events
    )
    assert any(
        event["type"] == "LANDED" and event["tile"]["tileId"] == 24 for event in events
    )
    assert any(
        event["type"] == "PLAYER_MOVED"
        and event.get("trigger") == "move_to_island"
        and event["toTileId"] == 8
        for event in events
    )
    assert state.require_player(1).current_tile_id == 8
    assert state.require_player(1).player_state == PlayerState.LOCKED
    assert state.require_player(1).state_duration == 2
