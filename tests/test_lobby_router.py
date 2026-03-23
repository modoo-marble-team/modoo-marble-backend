from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.game.enums import PlayerState
from app.game.models import GameState, PlayerGameState
from app.game.state import INITIAL_BALANCE
from app.routers import lobby


class FakeSio:
    def __init__(self) -> None:
        self.emitted: list[dict] = []

    async def emit(self, event: str, data: dict, room=None) -> None:
        self.emitted.append({"event": event, "data": data, "room": room})


def make_game_state() -> GameState:
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
        tiles={},
    )


def test_start_room_game_accepts_object_game_state(monkeypatch):
    room = {
        "id": "room-1",
        "players": [
            {"id": "1", "nickname": "host"},
            {"id": "2", "nickname": "guest"},
        ],
    }
    game_state = make_game_state()
    sio = FakeSio()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sio=sio)))
    auth = SimpleNamespace(user_id=1)
    start_timer_calls: list[tuple[str, object]] = []

    async def fake_start_game(*, room_id: str, user_id: int):
        assert room_id == "room-1"
        assert user_id == 1
        return room, game_state

    async def fake_emit_lobby_updated(
        _request, action: str, payload_room: dict
    ) -> None:
        assert action == "status_changed"
        assert payload_room is room

    monkeypatch.setattr(lobby.room_service, "start_game", fake_start_game)
    monkeypatch.setattr(lobby, "_emit_lobby_updated", fake_emit_lobby_updated)
    monkeypatch.setattr(
        lobby,
        "start_turn_timer",
        lambda game_id, sio_instance: start_timer_calls.append((game_id, sio_instance)),
    )

    result = asyncio.run(lobby.start_room_game("room-1", request, auth))

    assert result == {"success": True, "game_id": "game-1"}
    assert start_timer_calls == [("game-1", sio)]
    assert any(
        item["event"] == "game_start"
        and item["room"] == "room:room-1"
        and item["data"]["game_id"] == "game-1"
        for item in sio.emitted
    )
    assert any(
        item["event"] == "game_start" and item["room"] == "user:1"
        for item in sio.emitted
    )
    assert any(
        item["event"] == "game_start" and item["room"] == "user:2"
        for item in sio.emitted
    )


def test_leave_room_routes_playing_room_to_immediate_game_leave(monkeypatch):
    sio = FakeSio()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sio=sio)))
    auth = SimpleNamespace(user_id=1)
    room = {
        "id": "room-1",
        "status": "playing",
        "game_id": "game-1",
        "players": [
            {"id": "1", "nickname": "host", "is_host": True, "is_ready": False},
            {"id": "2", "nickname": "guest", "is_host": False, "is_ready": False},
        ],
    }

    async def fake_get_room(room_id: str) -> dict | None:
        assert room_id == "room-1"
        return room

    async def fake_leave_game_for_user(*, game_id: str, user_id: int) -> bool:
        assert game_id == "game-1"
        assert user_id == 1
        return True

    monkeypatch.setattr(lobby.room_service, "get_room", fake_get_room)
    monkeypatch.setattr(lobby, "leave_game_for_user", fake_leave_game_for_user)

    result = asyncio.run(lobby.leave_room("room-1", request, auth))

    assert result == {"success": True, "new_host_id": "2"}
    assert sio.emitted == []
