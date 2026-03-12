from __future__ import annotations

from app.game.actions.end_turn import process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.enums import PlayerState
from app.game.presentation import serialize_game_snapshot
from app.game.rules import (
    process_prompt_response,
    process_sell_property_action,
    resolve_landing,
)


def apply_patches(state: dict, patches: list[dict]) -> None:
    for patch in patches:
        keys = patch["path"].split(".")
        target = state
        for key in keys[:-1]:
            target = target[key]

        last_key = keys[-1]
        if patch["op"] == "set":
            target[last_key] = patch["value"]
        elif patch["op"] == "inc":
            target[last_key] = target[last_key] + patch["value"]
        elif patch["op"] == "push":
            target[last_key].append(patch["value"])
        elif patch["op"] == "remove":
            if isinstance(target[last_key], list):
                target[last_key].remove(patch["value"])
            else:
                del target[last_key]


def make_state() -> dict:
    return {
        "game_id": "1",
        "room_id": "room-1",
        "revision": 0,
        "turn": 1,
        "round": 1,
        "current_player_id": 1,
        "status": "playing",
        "phase": "WAIT_ROLL",
        "pending_prompt": None,
        "players": {
            "1": {
                "user_id": 1,
                "nickname": "host",
                "balance": 5000,
                "current_tile_id": 0,
                "state": PlayerState.NORMAL,
                "state_duration": 0,
                "consecutive_doubles": 0,
                "owned_tile_ids": [],
                "building_levels": {},
                "turn_order": 0,
            },
            "2": {
                "user_id": 2,
                "nickname": "guest",
                "balance": 5000,
                "current_tile_id": 0,
                "state": PlayerState.NORMAL,
                "state_duration": 0,
                "consecutive_doubles": 0,
                "owned_tile_ids": [],
                "building_levels": {},
                "turn_order": 1,
            },
        },
        "tiles": {
            str(tile_id): {"owner_id": None, "building_level": 0}
            for tile_id in [1, 2, 4, 5, 6, 9, 11, 12, 13, 14, 15, 17, 18, 19, 21, 22, 23, 25, 26, 28, 29, 31]
        },
    }


def test_minimum_gameplay_turn_rotation(monkeypatch):
    state = make_state()
    dice_values = iter([1, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    roll_events, roll_patches = process_roll_dice(state, 1)
    apply_patches(state, roll_patches)
    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)
    state["revision"] += 1

    snapshot = serialize_game_snapshot(state)

    assert [event["type"] for event in roll_events][:3] == [
        "DICE_ROLLED",
        "PLAYER_MOVED",
        "LANDED",
    ]
    assert end_events[0]["type"] == "TURN_ENDED"
    assert snapshot["players"][0]["position"] == 3
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

    prompt = state["pending_prompt"]
    assert prompt is not None
    assert prompt["type"] == "BUY_OR_SKIP"
    assert state["phase"] == "WAIT_PROMPT"
    assert events[-1]["type"] == "LANDED"

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt["prompt_id"],
        choice="BUY",
    )
    apply_patches(state, prompt_patches)
    end_events, end_patches = process_end_turn(state, 1)
    apply_patches(state, end_patches)

    assert any(event["type"] == "BOUGHT_PROPERTY" for event in prompt_events)
    assert state["tiles"]["4"]["owner_id"] == 1
    assert state["players"]["1"]["balance"] == 4500
    assert state["players"]["1"]["owned_tile_ids"] == [4]
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state["current_player_id"] == 2
    assert state["phase"] == "WAIT_ROLL"


def test_owned_property_requires_toll_prompt_and_transfers_money(monkeypatch):
    state = make_state()
    state["tiles"]["4"]["owner_id"] = 1
    state["players"]["1"]["owned_tile_ids"] = [4]
    state["current_player_id"] = 2
    dice_values = iter([2, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    events, patches = process_roll_dice(state, 2)
    apply_patches(state, patches)

    prompt = state["pending_prompt"]
    assert prompt is not None
    assert prompt["type"] == "PAY_TOLL"
    assert any(event["type"] == "LANDED" for event in events)

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=2,
        prompt_id=prompt["prompt_id"],
        choice="PAY_TOLL",
    )
    apply_patches(state, prompt_patches)
    end_events, end_patches = process_end_turn(state, 2)
    apply_patches(state, end_patches)

    assert any(event["type"] == "PAID_TOLL" for event in prompt_events)
    assert state["players"]["1"]["balance"] == 5500
    assert state["players"]["2"]["balance"] == 4500
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state["current_player_id"] == 1


def test_sell_property_action_refunds_money_and_releases_tile():
    state = make_state()
    state["tiles"]["4"]["owner_id"] = 1
    state["players"]["1"]["owned_tile_ids"] = [4]

    events, patches = process_sell_property_action(
        state,
        player_id=1,
        tile_id=4,
        building_level=0,
    )
    apply_patches(state, patches)

    assert any(event["type"] == "SOLD_PROPERTY" for event in events)
    assert state["players"]["1"]["balance"] == 5500
    assert state["tiles"]["4"]["owner_id"] is None
    assert state["players"]["1"]["owned_tile_ids"] == []


def test_travel_prompt_moves_to_selected_tile_and_chains_into_tile_prompt():
    state = make_state()
    state["players"]["1"]["current_tile_id"] = 16

    travel_events, travel_patches = resolve_landing(state, 1, 16)
    apply_patches(state, travel_patches)

    prompt = state["pending_prompt"]
    assert prompt is not None
    assert prompt["type"] == "TRAVEL_SELECT"
    assert travel_events == []

    response_events, response_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt["prompt_id"],
        choice="CONFIRM",
        payload={"targetTileId": 4},
    )
    apply_patches(state, response_patches)

    assert any(event["type"] == "PLAYER_MOVED" for event in response_events)
    assert state["players"]["1"]["current_tile_id"] == 4
    assert state["pending_prompt"] is not None
    assert state["pending_prompt"]["type"] == "BUY_OR_SKIP"
    assert state["phase"] == "WAIT_PROMPT"

