from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

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
        self.emitted.append(
            {
                "event": event,
                "data": data,
                "to": to,
                "room": room,
            }
        )

    async def enter_room(self, sid: str, room: str) -> None:
        self.entered_rooms.append((sid, room))

    async def leave_room(self, sid: str, room: str) -> None:
        return None


class FakeRuntime:
    def __init__(self) -> None:
        self.active_games: list[tuple[int, str]] = []

    async def set_active_game(self, *, user_id: int, game_id: str) -> None:
        self.active_games.append((user_id, game_id))

    async def build_and_store_patch_packet(
        self,
        *,
        state: dict,
        events: list[dict],
        patches: list[dict],
        include_snapshot: bool = False,
    ) -> dict:
        return {
            "gameId": state["game_id"],
            "revision": state["revision"],
            "turn": state["turn"],
            "events": events,
            "patch": patches,
            "snapshot": state if include_snapshot else None,
        }


def make_state() -> dict:
    return {
        "game_id": "game-1",
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
                "playerState": "NORMAL",
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
                "playerState": "NORMAL",
                "stateDuration": 0,
                "consecutiveDoubles": 0,
                "ownedTiles": [],
                "buildingLevels": {},
                "turnOrder": 1,
            },
        },
        "tiles": {},
    }


@pytest.mark.asyncio
async def test_build_and_store_patch_packet_preserves_patch_ops(monkeypatch):
    runtime = GameSyncRuntime(FakeSio())
    stored: list[tuple[str, dict]] = []

    async def fake_append_patch_packet(*, game_id: str, packet: dict) -> None:
        stored.append((game_id, packet))

    monkeypatch.setattr(runtime, "append_patch_packet", fake_append_patch_packet)

    state = make_state()
    state["revision"] = 3
    packet = await runtime.build_and_store_patch_packet(
        state=state,
        events=[{"type": "DICE_ROLLED", "playerId": 1}],
        patches=[{"op": "set", "path": "players.1.currentTileId", "value": 4}],
        include_snapshot=False,
    )

    assert packet["patch"] == [
        {"op": "set", "path": "players.1.currentTileId", "value": 4}
    ]
    assert stored == [("game-1", packet)]


@pytest.mark.asyncio
async def test_game_action_joins_game_room_before_broadcast(monkeypatch):
    sio = FakeSio()
    runtime = FakeRuntime()
    state = make_state()

    monkeypatch.setattr(
        "app.game.socket_handlers.init_game_sync_runtime", lambda _sio: runtime
    )

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    async def fake_get_game_state(_game_id: str) -> dict:
        return state

    async def fake_save_game_state(_game_id: str, _state: dict) -> None:
        return None

    def fake_process_roll_dice(_state: dict, _user_id: int):
        return (
            [{"type": "DICE_ROLLED", "playerId": 1}],
            [{"op": "set", "path": "players.1.currentTileId", "value": 4}],
        )

    monkeypatch.setattr("app.game.socket_handlers.game_lock", fake_game_lock)
    monkeypatch.setattr("app.game.socket_handlers.get_game_state", fake_get_game_state)
    monkeypatch.setattr(
        "app.game.socket_handlers.save_game_state", fake_save_game_state
    )
    monkeypatch.setattr(
        "app.game.socket_handlers.process_roll_dice",
        fake_process_roll_dice,
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
            "actionId": "action-1",
            "type": "ROLL_DICE",
        },
    )

    assert ("sid-1", "game:game-1") in sio.entered_rooms
    assert any(
        item["event"] == "game:patch" and item["room"] == "game:game-1"
        for item in sio.emitted
    )
    assert any(
        item["event"] == "game:ack" and item["to"] == "sid-1" for item in sio.emitted
    )
