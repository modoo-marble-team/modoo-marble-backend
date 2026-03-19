from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.game.enums import PlayerState
from app.game.models import (
    GameState,
    PendingPrompt,
    PlayerGameState,
    PromptChoice,
    TileGameState,
)
from app.game.socket_handlers import register_game_handlers
from app.game.sync_runtime import GameSyncRuntime


class FakeSio:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.entered_rooms: list[tuple[str, str]] = []
        self.emitted: list[dict] = []

    def on(self, event: str):
        def decorator(func):
            self.handlers[event] = func
            return func

        return decorator

    async def emit(self, event: str, data: dict, *, to=None, room=None) -> None:
        self.emitted.append({"event": event, "data": data, "to": to, "room": room})

    async def enter_room(self, sid: str, room: str) -> None:
        self.entered_rooms.append((sid, room))

    async def leave_room(self, sid: str, room: str) -> None:
        return None


class FakeRuntime:
    def __init__(self) -> None:
        self.active_games: list[tuple[int, str]] = []
        self.synced: list[tuple[str, int, str]] = []

    async def set_active_game(self, *, user_id: int, game_id: str) -> None:
        self.active_games.append((user_id, game_id))

    async def handle_sync(
        self,
        *,
        sid: str,
        user_id: int,
        game_id: str,
        known_revision: int,
    ) -> GameState:
        self.synced.append((sid, user_id, game_id))
        return make_state()

    async def build_and_store_patch_packet(
        self,
        *,
        state: GameState,
        events: list[dict],
        patches: list[dict],
        include_snapshot: bool = False,
    ) -> dict:
        return {
            "gameId": state.game_id,
            "revision": state.revision,
            "turn": state.turn,
            "events": events,
            "patch": patches,
            "snapshot": state.to_json() if include_snapshot else None,
        }


def make_state() -> GameState:
    return GameState(
        game_id="game-1",
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
        tiles={},
    )


@pytest.mark.asyncio
async def test_build_and_store_patch_packet_preserves_patch_ops(monkeypatch):
    runtime = GameSyncRuntime(FakeSio())
    stored: list[tuple[str, dict]] = []

    async def fake_append_patch_packet(*, game_id: str, packet: dict) -> None:
        stored.append((game_id, packet))

    monkeypatch.setattr(runtime, "append_patch_packet", fake_append_patch_packet)

    state = make_state()
    state.revision = 3
    packet = await runtime.build_and_store_patch_packet(
        state=state,
        events=[{"type": "DICE_ROLLED", "playerId": 1}],
        patches=[{"op": "set", "path": "players.1.current_tile_id", "value": 4}],
        include_snapshot=False,
    )

    assert packet["patch"] == [
        {"op": "set", "path": "players.1.currentTileId", "value": 4}
    ]
    assert stored == [("game-1", packet)]


@pytest.mark.asyncio
async def test_handle_sync_includes_snapshot_for_initial_revision_zero(monkeypatch):
    sio = FakeSio()
    runtime = GameSyncRuntime(sio)
    state = make_state()

    async def fake_get_game_state(_game_id: str) -> GameState:
        return state

    async def fake_update_status(*, user_id: str, status: str) -> None:
        return None

    async def fake_set_active_game(*, user_id: int, game_id: str) -> None:
        return None

    async def fake_get_disconnected_at(*, game_id: str, player_id: int):
        return None

    async def fake_clear_disconnected_at(*, game_id: str, player_id: int) -> None:
        return None

    async def fake_emit_reconnected_if_needed(**_kwargs) -> None:
        return None

    monkeypatch.setattr("app.game.sync_runtime.get_game_state", fake_get_game_state)
    monkeypatch.setattr("app.game.sync_runtime.update_status", fake_update_status)
    monkeypatch.setattr(runtime, "set_active_game", fake_set_active_game)
    monkeypatch.setattr(runtime, "get_disconnected_at", fake_get_disconnected_at)
    monkeypatch.setattr(runtime, "clear_disconnected_at", fake_clear_disconnected_at)
    monkeypatch.setattr(
        runtime,
        "_emit_reconnected_if_needed",
        fake_emit_reconnected_if_needed,
    )

    synced_state = await runtime.handle_sync(
        sid="sid-1",
        user_id=1,
        game_id="game-1",
        known_revision=0,
    )

    assert synced_state is state
    patch_events = [item for item in sio.emitted if item["event"] == "game:patch"]
    assert len(patch_events) == 1
    assert patch_events[0]["to"] == "sid-1"
    assert patch_events[0]["data"]["revision"] == 0
    assert patch_events[0]["data"]["snapshot"] is not None
    assert patch_events[0]["data"]["snapshot"]["gameId"] == "game-1"


@pytest.mark.asyncio
async def test_game_action_joins_game_room_before_broadcast(monkeypatch):
    sio = FakeSio()
    runtime = FakeRuntime()
    state = make_state()

    monkeypatch.setattr(
        "app.game.socket_handlers.init_game_sync_runtime",
        lambda _sio: runtime,
    )

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    async def fake_get_game_state(_game_id: str) -> GameState:
        return state

    async def fake_save_game_state(_game_id: str, _state: GameState) -> None:
        return None

    def fake_process_roll_dice(_state: GameState, _user_id: int):
        return (
            [{"type": "DICE_ROLLED", "playerId": 1}],
            [{"op": "set", "path": "players.1.current_tile_id", "value": 4}],
        )

    monkeypatch.setattr("app.game.socket_handlers.game_lock", fake_game_lock)
    monkeypatch.setattr("app.game.socket_handlers.get_game_state", fake_get_game_state)
    monkeypatch.setattr(
        "app.game.socket_handlers.save_game_state", fake_save_game_state
    )
    monkeypatch.setattr(
        "app.game.socket_handlers.process_roll_dice", fake_process_roll_dice
    )
    monkeypatch.setattr(
        "app.game.socket_handlers.start_turn_timer", lambda *_args: None
    )

    register_game_handlers(sio, {"sid-1": 1})
    handler = sio.handlers["game:action"]

    await handler(
        "sid-1",
        {"gameId": "game-1", "actionId": "action-1", "type": "ROLL_DICE"},
    )

    assert ("sid-1", "game:game-1") in sio.entered_rooms
    assert any(
        item["event"] == "game:patch" and item["room"] == "game:game-1"
        for item in sio.emitted
    )
    assert any(
        item["event"] == "game:ack" and item["to"] == "sid-1" for item in sio.emitted
    )


@pytest.mark.asyncio
async def test_game_sync_does_not_emit_duplicate_patch(monkeypatch):
    sio = FakeSio()
    runtime = FakeRuntime()
    state = make_state()

    async def fake_handle_sync(
        *,
        sid: str,
        user_id: int,
        game_id: str,
        known_revision: int,
    ) -> GameState:
        runtime.synced.append((sid, user_id, game_id))
        await sio.emit(
            "game:patch",
            {
                "gameId": game_id,
                "revision": state.revision,
                "turn": state.turn,
                "events": [{"type": "SYNCED"}],
                "patch": [],
                "snapshot": state.to_json(),
            },
            to=sid,
        )
        return state

    runtime.handle_sync = fake_handle_sync  # type: ignore[method-assign]

    monkeypatch.setattr(
        "app.game.socket_handlers.init_game_sync_runtime",
        lambda _sio: runtime,
    )

    register_game_handlers(sio, {"sid-1": 1})
    handler = sio.handlers["game:sync"]

    await handler("sid-1", {"gameId": "game-1", "knownRevision": 0})

    patch_events = [item for item in sio.emitted if item["event"] == "game:patch"]
    assert len(patch_events) == 1
    assert patch_events[0]["to"] == "sid-1"


@pytest.mark.asyncio
async def test_legacy_travel_action_uses_pending_travel_prompt(monkeypatch):
    sio = FakeSio()
    runtime = FakeRuntime()
    state = make_state()
    state.require_player(1).current_tile_id = 16
    state.phase = "WAIT_PROMPT"
    state.tiles = {4: TileGameState(owner_id=None, building_level=0)}
    state.pending_prompt = PendingPrompt(
        prompt_id="prompt-travel-1",
        type="TRAVEL_SELECT",
        player_id=1,
        title="Travel",
        message="Choose destination",
        timeout_sec=30,
        choices=[PromptChoice(id="confirm", label="Go", value="CONFIRM")],
        payload={"tileId": 16, "tileName": "Travel"},
        default_choice="SKIP",
    )

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    async def fake_get_game_state(_game_id: str) -> GameState:
        return state

    async def fake_save_game_state(_game_id: str, _state: GameState) -> None:
        return None

    monkeypatch.setattr(
        "app.game.socket_handlers.init_game_sync_runtime",
        lambda _sio: runtime,
    )
    monkeypatch.setattr("app.game.socket_handlers.game_lock", fake_game_lock)
    monkeypatch.setattr("app.game.socket_handlers.get_game_state", fake_get_game_state)
    monkeypatch.setattr(
        "app.game.socket_handlers.save_game_state", fake_save_game_state
    )
    monkeypatch.setattr(
        "app.game.socket_handlers.start_turn_timer", lambda *_args: None
    )

    register_game_handlers(sio, {"sid-1": 1})
    handler = sio.handlers["game:action"]

    await handler(
        "sid-1",
        {
            "gameId": "game-1",
            "actionId": "travel-1",
            "type": "TRAVEL",
            "payload": {"toIndex": 4},
        },
    )

    assert state.require_player(1).current_tile_id == 4
    assert state.pending_prompt is not None
    assert state.pending_prompt.type == "BUY_OR_SKIP"
    assert any(
        item["event"] == "game:ack"
        and item["data"]["ok"] is True
        and item["data"]["type"] == "TRAVEL"
        for item in sio.emitted
    )
    assert any(
        item["event"] == "game:prompt" and item["room"] == "user:1"
        for item in sio.emitted
    )
