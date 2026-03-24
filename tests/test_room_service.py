from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.room_service import RoomService


def make_playing_room() -> dict:
    return {
        "id": "room-1",
        "title": "테스트 방",
        "status": "playing",
        "is_private": False,
        "password": None,
        "max_players": 4,
        "host_user_id": "1",
        "game_id": "game-1",
        "players": [
            {"id": "1", "nickname": "host", "is_ready": False, "is_host": True},
            {"id": "2", "nickname": "guest", "is_ready": True, "is_host": False},
        ],
        "chat_messages": [],
    }


@pytest.mark.asyncio
async def test_join_room_existing_member_reconciles_finished_game_room(monkeypatch):
    service = RoomService()
    stale_room = make_playing_room()
    cleaned_room = {
        **stale_room,
        "status": "waiting",
        "game_id": None,
        "players": [
            {**player, "is_ready": False} for player in stale_room["players"]
        ],
    }

    monkeypatch.setattr(service, "_require_room", AsyncMock(return_value=stale_room))
    monkeypatch.setattr(service, "_get_user", AsyncMock())
    monkeypatch.setattr(service, "_get_user_room_id", AsyncMock(return_value="room-1"))
    monkeypatch.setattr(service, "_set_user_room_id", AsyncMock())
    monkeypatch.setattr(service, "finish_game_room", AsyncMock(return_value=cleaned_room))
    monkeypatch.setattr(
        "app.services.room_service.get_game_state",
        AsyncMock(return_value=None),
    )

    room = await service.join_room(room_id="room-1", user_id=2, password=None)

    assert room["status"] == "waiting"
    assert room["game_id"] is None
    assert all(player["is_ready"] is False for player in room["players"])
    service.finish_game_room.assert_awaited_once_with(room_id="room-1")


@pytest.mark.asyncio
async def test_join_room_existing_member_keeps_active_playing_room(monkeypatch):
    service = RoomService()
    active_room = make_playing_room()
    active_game_state = type("ActiveGameState", (), {"status": "playing"})()

    monkeypatch.setattr(service, "_require_room", AsyncMock(return_value=active_room))
    monkeypatch.setattr(service, "_get_user", AsyncMock())
    monkeypatch.setattr(service, "_get_user_room_id", AsyncMock(return_value="room-1"))
    monkeypatch.setattr(service, "_set_user_room_id", AsyncMock())
    monkeypatch.setattr(service, "finish_game_room", AsyncMock())
    monkeypatch.setattr(
        "app.services.room_service.get_game_state",
        AsyncMock(return_value=active_game_state),
    )

    room = await service.join_room(room_id="room-1", user_id=2, password=None)

    assert room["status"] == "playing"
    assert room["game_id"] == "game-1"
    assert room["players"][1]["is_ready"] is True
    service.finish_game_room.assert_not_awaited()
