from __future__ import annotations

from app.game.actions.end_turn import MAX_ROUNDS, process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.enums import PlayerState, ServerEventType
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
                "playerId": 1,
                "nickname": "host",
                "balance": 5000,
                "currentTileId": 0,
                "playerState": PlayerState.NORMAL,
                "stateDuration": 0,
                "consecutiveDoubles": 0,
                "ownedTiles": [],
                "buildingLevels": {},
                "turnOrder": 0,
            },
            "2": {
                "playerId": 2,
                "nickname": "guest",
                "balance": 5000,
                "currentTileId": 0,
                "playerState": PlayerState.NORMAL,
                "stateDuration": 0,
                "consecutiveDoubles": 0,
                "ownedTiles": [],
                "buildingLevels": {},
                "turnOrder": 1,
            },
        },
        "tiles": {
            str(tile_id): {"ownerId": None, "buildingLevel": 0}
            for tile_id in [
                1,
                2,
                4,
                5,
                6,
                9,
                11,
                12,
                13,
                14,
                15,
                17,
                18,
                19,
                21,
                22,
                23,
                25,
                26,
                28,
                29,
                31,
            ]
        },
    }


# ─────────────────────────────────────────────────────────────────
# 기본 턴 로테이션
# ─────────────────────────────────────────────────────────────────


def test_minimum_gameplay_turn_rotation(monkeypatch):
    """주사위 합 3 → 찬스(tile 3) 착지 후 턴 종료 시 다음 플레이어로 전환된다.
    찬스 카드 무작위성을 GAIN_MONEY로 고정해 이동/prompt 연쇄를 방지한다."""
    state = make_state()
    dice_values = iter([1, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )
    # MOVE_FORWARD/MOVE_BACKWARD/STEAL/GIVE 카드가 나올 경우 prompt 또는 체인 이동이
    # 발생할 수 있으므로 안전한 GAIN_MONEY로 고정한다.
    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "GAIN_MONEY",
            "amount": 100,
            "description": "테스트 보너스",
        },
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
    assert snapshot["players"][0]["currentTileId"] == 3
    assert snapshot["currentPlayerId"] == "2"
    assert snapshot["round"] == 1


# ─────────────────────────────────────────────────────────────────
# 부동산 매입 프롬프트
# ─────────────────────────────────────────────────────────────────


def test_property_landing_creates_buy_prompt_and_purchase(monkeypatch):
    """소유자 없는 부동산 착지 → BUY_OR_SKIP prompt, BUY 선택 시 소유권·잔액 반영."""
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
    assert state["tiles"]["4"]["ownerId"] == 1
    assert state["players"]["1"]["balance"] == 4500
    assert state["players"]["1"]["ownedTiles"] == [4]
    assert end_events[0]["type"] == "TURN_ENDED"
    assert state["current_player_id"] == 2
    assert state["phase"] == "WAIT_ROLL"


def test_property_landing_skip_does_not_transfer_ownership(monkeypatch):
    """BUY_OR_SKIP에서 SKIP 선택 시 소유권 이전 없이 잔액도 그대로다."""
    state = make_state()
    dice_values = iter([2, 2])

    monkeypatch.setattr(
        "app.game.actions.roll_dice.random.randint",
        lambda _start, _end: next(dice_values),
    )

    _events, patches = process_roll_dice(state, 1)
    apply_patches(state, patches)

    prompt = state["pending_prompt"]
    assert prompt is not None
    assert prompt["type"] == "BUY_OR_SKIP"

    prompt_events, prompt_patches = process_prompt_response(
        state,
        player_id=1,
        prompt_id=prompt["prompt_id"],
        choice="SKIP",
    )
    apply_patches(state, prompt_patches)

    assert state["tiles"]["4"]["ownerId"] is None
    assert state["players"]["1"]["balance"] == 5000
    assert not any(event["type"] == "BOUGHT_PROPERTY" for event in prompt_events)


# ─────────────────────────────────────────────────────────────────
# 통행료 납부
# ─────────────────────────────────────────────────────────────────


def test_owned_property_requires_toll_prompt_and_transfers_money(monkeypatch):
    """타인 소유 부동산 착지 → PAY_TOLL prompt, 납부 시 소유자 잔액 증가·방문자 잔액 감소."""
    state = make_state()
    state["tiles"]["4"]["ownerId"] = 1
    state["players"]["1"]["ownedTiles"] = [4]
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


# ─────────────────────────────────────────────────────────────────
# 부동산 매각
# ─────────────────────────────────────────────────────────────────


def test_sell_property_action_refunds_money_and_releases_tile():
    """부동산 매각 시 환불 금액이 잔액에 반영되고 소유권이 해제된다."""
    state = make_state()
    state["tiles"]["4"]["ownerId"] = 1
    state["players"]["1"]["ownedTiles"] = [4]

    events, patches = process_sell_property_action(
        state,
        player_id=1,
        tile_id=4,
        building_level=0,
    )
    apply_patches(state, patches)

    assert any(event["type"] == "SOLD_PROPERTY" for event in events)
    assert state["players"]["1"]["balance"] == 5500
    assert state["tiles"]["4"]["ownerId"] is None
    assert state["players"]["1"]["ownedTiles"] == []


# ─────────────────────────────────────────────────────────────────
# 여행 타일 연쇄 프롬프트
# ─────────────────────────────────────────────────────────────────


def test_travel_prompt_moves_to_selected_tile_and_chains_into_tile_prompt():
    """여행(tile 16) 착지 → TRAVEL_SELECT prompt, 부동산 선택 시 BUY_OR_SKIP으로 체인된다."""
    state = make_state()
    state["players"]["1"]["currentTileId"] = 16

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
    assert state["players"]["1"]["currentTileId"] == 4
    assert state["pending_prompt"] is not None
    assert state["pending_prompt"]["type"] == "BUY_OR_SKIP"
    assert state["phase"] == "WAIT_PROMPT"


# ─────────────────────────────────────────────────────────────────
# 파산 처리 및 턴 스킵
# ─────────────────────────────────────────────────────────────────


def test_bankrupt_player_is_skipped_in_turn_order(monkeypatch):
    """파산한 플레이어는 턴 순서에서 제외되고, 남은 플레이어가 다음 턴을 받는다."""
    state = make_state()
    # 플레이어 2를 파산 처리 → player 1이 계속 진행
    state["players"]["2"]["playerState"] = PlayerState.BANKRUPT

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

    # 파산한 player 2를 건너뛰어 다시 player 1의 턴
    assert state["current_player_id"] == 1
    assert end_events[0]["type"] == "TURN_ENDED"
    assert end_events[0]["nextPlayerId"] == 1


def test_last_player_standing_triggers_game_over():
    """활성 플레이어가 1명뿐일 때 process_end_turn이 game_over를 반환한다."""
    state = make_state()
    state["players"]["2"]["playerState"] = PlayerState.BANKRUPT

    events, patches = process_end_turn(state, 1)
    apply_patches(state, patches)

    game_over_events = [e for e in events if e["type"] == ServerEventType.GAME_OVER]
    assert len(game_over_events) == 1
    assert game_over_events[0]["reason"] == "last_player_standing"
    assert state["status"] == "finished"


# ─────────────────────────────────────────────────────────────────
# 20라운드 종료 (중복 블록 수정 검증 포함)
# ─────────────────────────────────────────────────────────────────


def test_max_rounds_triggers_game_over_exactly_once():
    """20라운드 초과 시 GAME_OVER 이벤트가 정확히 1번만 발생해야 한다.
    end_turn.py에 있던 중복 블록이 제거되었는지 확인하는 회귀 테스트."""
    state = make_state()
    # player2(turnOrder=1) → player1(turnOrder=0)으로 넘어갈 때 round+1
    state["round"] = MAX_ROUNDS
    state["current_player_id"] = 2

    events, patches = process_end_turn(state, 2)
    apply_patches(state, patches)

    game_over_events = [e for e in events if e["type"] == ServerEventType.GAME_OVER]
    assert len(game_over_events) == 1, (
        f"GAME_OVER가 {len(game_over_events)}번 발생했습니다 (중복 블록 버그 재현)"
    )
    assert game_over_events[0]["reason"] == "max_rounds"
    assert state["status"] == "finished"


# ─────────────────────────────────────────────────────────────────
# 연속 더블 카운터 초기화
# ─────────────────────────────────────────────────────────────────


def test_consecutive_doubles_reset_after_turn_end(monkeypatch):
    """턴 종료 시 consecutiveDoubles 카운터가 0으로 초기화되어야 한다."""
    state = make_state()
    state["players"]["1"]["consecutiveDoubles"] = 2

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

    assert state["players"]["1"]["consecutiveDoubles"] == 0


# ─────────────────────────────────────────────────────────────────
# 찬스 카드 효과
# ─────────────────────────────────────────────────────────────────


def test_chance_card_gain_money_increases_balance(monkeypatch):
    """찬스 카드 GAIN_MONEY 효과 시 잔액이 정확히 증가해야 한다."""
    state = make_state()
    state["players"]["1"]["currentTileId"] = 3  # 찬스 타일

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "GAIN_MONEY",
            "amount": 300,
            "description": "복권 당첨!",
        },
    )

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    assert state["players"]["1"]["balance"] == 5300
    assert any(e["type"] == "CHANCE_RESOLVED" for e in events)


def test_chance_card_lose_money_decreases_balance(monkeypatch):
    """찬스 카드 LOSE_MONEY 효과 시 잔액이 정확히 감소해야 한다."""
    state = make_state()
    state["players"]["1"]["currentTileId"] = 3  # 찬스 타일

    monkeypatch.setattr(
        "app.game.rules.random.choice",
        lambda _pool: {
            "type": "LOSE_MONEY",
            "amount": 150,
            "description": "교통 벌금!",
        },
    )

    events, patches = resolve_landing(state, 1, 3)
    apply_patches(state, patches)

    assert state["players"]["1"]["balance"] == 4850
    assert any(e["type"] == "CHANCE_RESOLVED" for e in events)
