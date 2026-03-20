from __future__ import annotations

from app.game.board import BOARD
from app.game.enums import PlayerState, TileType
from app.game.models import (
    GameState,
    PendingPrompt,
    PlayerGameState,
    PromptChoice,
    TileGameState,
)
from app.game.state import INITIAL_BALANCE
from app.game.timer import process_turn_timeout


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


def test_turn_timeout_auto_rolls_and_skips_optional_actions(monkeypatch):
    state = make_state()
    dice_values = iter([2, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, _patches = process_turn_timeout(state)

    assert [event["type"] for event in events[:3]] == [
        "DICE_ROLLED",
        "PLAYER_MOVED",
        "LANDED",
    ]
    assert events[-1]["type"] == "TURN_ENDED"
    assert state.require_player(1).current_tile_id == 4
    assert state.tile(4).owner_id is None
    assert state.pending_prompt is None
    assert state.current_player_id == 2
    assert state.phase == "WAIT_ROLL"


def test_turn_timeout_pays_toll_and_skips_acquisition(monkeypatch):
    state = make_state()
    state.current_player_id = 2
    state.tile(4).owner_id = 1
    state.tile(4).building_level = 2
    state.require_player(1).owned_tiles = [4]
    state.require_player(1).building_levels = {4: 2}
    dice_values = iter([2, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, _patches = process_turn_timeout(state)

    event_types = [event["type"] for event in events]
    assert "PAID_TOLL" in event_types
    assert "ACQUIRED_PROPERTY" not in event_types
    assert event_types[-1] == "TURN_ENDED"
    assert state.tile(4).owner_id == 1
    assert state.pending_prompt is None
    assert state.current_player_id == 1
    assert state.phase == "WAIT_ROLL"


def test_turn_timeout_randomizes_travel_destination(monkeypatch):
    state = make_state()
    state.phase = "WAIT_PROMPT"
    state.require_player(1).current_tile_id = 16
    state.pending_prompt = PendingPrompt(
        prompt_id="prompt-travel-1",
        type="TRAVEL_SELECT",
        player_id=1,
        title="여행",
        message="이동할 목적지를 선택하세요.",
        timeout_sec=30,
        choices=[
            PromptChoice(id="confirm", label="선택", value="CONFIRM"),
            PromptChoice(id="skip", label="건너뛰기", value="SKIP"),
        ],
        payload={"tileId": 16, "tileName": "여행"},
        default_choice="SKIP",
    )

    monkeypatch.setattr("app.game.timer.random.choice", lambda candidates: 4)

    events, _patches = process_turn_timeout(state)

    event_types = [event["type"] for event in events]
    assert "PLAYER_MOVED" in event_types
    assert "LANDED" in event_types
    assert event_types[-1] == "TURN_ENDED"
    assert state.require_player(1).current_tile_id == 4
    assert state.pending_prompt is None
    assert state.current_player_id == 2
