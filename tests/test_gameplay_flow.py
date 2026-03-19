from __future__ import annotations

from app.game.actions.end_turn import MAX_ROUNDS, process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.board import BOARD
from app.game.enums import PlayerState, ServerEventType, TileType
from app.game.errors import GameActionError
from app.game.models import GameState, PlayerGameState, TileGameState
from app.game.presentation import serialize_game_snapshot
from app.game.rules import (
    process_prompt_response,
    process_sell_property_action,
    resolve_landing,
)
from app.game.state import apply_patches


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
                balance=5000,
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
                balance=5000,
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
            "amount": 100,
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


def test_property_landing_creates_buy_prompt_and_purchase(monkeypatch):
    state = make_state()
    dice_values = iter([2, 2])

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
    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert any(event["type"] == "BOUGHT_PROPERTY" for event in prompt_events)
    assert state.tile(4).owner_id == 1
    assert state.require_player(1).balance == 4500
    assert state.require_player(1).owned_tiles == [4]
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state.current_player_id == 2
    assert state.phase == "WAIT_ROLL"


def test_property_landing_skip_does_not_transfer_ownership(monkeypatch):
    state = make_state()
    dice_values = iter([2, 2])

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
    assert state.require_player(1).balance == 5000
    assert not any(event["type"] == "BOUGHT_PROPERTY" for event in prompt_events)


def test_owned_property_landing_prompts_for_toll_before_acquisition(monkeypatch):
    state = make_state()
    state.tile(4).owner_id = 1
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 0}
    state.current_player_id = 2
    dice_values = iter([2, 2])

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
    assert state.require_player(1).balance == 5500
    assert state.require_player(2).balance == 4500
    assert state.pending_prompt is not None
    assert state.pending_prompt.type == "ACQUISITION_OR_SKIP"
    assert state.phase == "WAIT_PROMPT"


def test_owned_property_landing_can_acquire_full_property_with_buildings(monkeypatch):
    state = make_state()
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    state.current_player_id = 2
    dice_values = iter([2, 2])

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
    assert acquisition_prompt.payload["acquisitionCost"] == 800
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
    assert state.require_player(1).balance == 5900
    assert state.require_player(2).balance == 4100
    assert state.require_player(1).owned_tiles == []
    assert state.require_player(1).building_levels == {}
    assert state.require_player(2).owned_tiles == [4]
    assert state.require_player(2).building_levels == {4: 2}


def test_owned_property_landing_skip_pays_toll_without_transfer(monkeypatch):
    state = make_state()
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    state.current_player_id = 2
    dice_values = iter([2, 2])

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
    assert state.require_player(1).balance == 5100
    assert state.require_player(2).balance == 4900


def test_property_acquisition_requires_enough_balance(monkeypatch):
    state = make_state()
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    state.require_player(2).balance = 700
    state.current_player_id = 2
    dice_values = iter([2, 2])

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


def test_sell_property_action_refunds_money_and_releases_tile():
    state = make_state()
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
    assert state.require_player(1).balance == 5500
    assert state.tile(4).owner_id is None
    assert state.require_player(1).owned_tiles == []


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


def test_bankrupt_player_is_skipped_in_turn_order(monkeypatch):
    state = make_state()
    state.players[3] = PlayerGameState(
        player_id=3,
        nickname="third",
        balance=5000,
        current_tile_id=0,
        player_state=PlayerState.NORMAL,
        state_duration=0,
        consecutive_doubles=0,
        owned_tiles=[],
        building_levels={},
        turn_order=2,
    )
    state.require_player(2).player_state = PlayerState.BANKRUPT

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: 1,
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

    events, patches = process_end_turn(state, 1)
    apply_patches(state, patches)

    game_over_events = [
        event for event in events if event["type"] == ServerEventType.GAME_OVER
    ]
    assert len(game_over_events) == 1
    assert game_over_events[0]["reason"] == "last_player_standing"
    assert state.status == "finished"


def test_max_rounds_triggers_game_over_exactly_once():
    state = make_state()
    state.round = MAX_ROUNDS
    state.current_player_id = 2

    events, patches = process_end_turn(state, 2)
    apply_patches(state, patches)

    game_over_events = [
        event for event in events if event["type"] == ServerEventType.GAME_OVER
    ]
    assert len(game_over_events) == 1
    assert game_over_events[0]["reason"] == "max_rounds"
    assert state.status == "finished"


def test_consecutive_doubles_reset_after_turn_end(monkeypatch):
    state = make_state()
    state.require_player(1).consecutive_doubles = 2

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: 1,
    )
    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {"type": "GAIN_MONEY", "amount": 0, "description": ""},
    )

    roll_events, roll_patches = process_roll_dice(state, 1)
    apply_patches(state, roll_patches)
    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert roll_events[0]["type"] == "DICE_ROLLED"
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state.require_player(1).consecutive_doubles == 0


def test_chance_card_gain_money_increases_balance(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "GAIN_MONEY",
            "amount": 300,
            "description": "jackpot",
        },
    )

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    assert state.require_player(1).balance == 5300
    assert any(event["type"] == "CHANCE_RESOLVED" for event in events)


def test_chance_card_lose_money_decreases_balance(monkeypatch):
    state = make_state()
    state.require_player(1).current_tile_id = 3

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "LOSE_MONEY",
            "amount": 150,
            "description": "fine",
        },
    )

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    assert state.require_player(1).balance == 4850
    assert any(event["type"] == "CHANCE_RESOLVED" for event in events)
