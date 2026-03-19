from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.game.enums import PlayerState
from app.game.models import GameState, PlayerGameState
from app.game.sync_runtime import GameSyncRuntime


def make_state() -> GameState:
    return GameState(
        game_id="game-1",
        room_id="room-1",
        revision=3,
        turn=5,
        round=2,
        current_player_id=1,
        status="finished",
        phase="GAME_OVER",
        pending_prompt=None,
        winner_id=1,
        players={
            1: PlayerGameState(
                player_id=1,
                nickname="host",
                balance=5300,
                current_tile_id=4,
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
                balance=0,
                current_tile_id=7,
                player_state=PlayerState.BANKRUPT,
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
async def test_finalize_finished_game_keeps_room_and_clears_game_keys(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    runtime._user_sids[1] = {"sid-1"}
    state = make_state()

    deleted_keys: list[str] = []

    class FakeRedis:
        async def delete(self, *keys: str) -> int:
            deleted_keys.extend(keys)
            return len(keys)

    room = {
        "id": "room-1",
        "title": "테스트 방",
        "status": "waiting",
        "max_players": 4,
        "is_private": False,
        "host_user_id": "1",
        "game_id": None,
        "players": [
            {"id": "1", "nickname": "host", "is_host": True, "is_ready": False},
            {"id": "2", "nickname": "guest", "is_host": False, "is_ready": False},
        ],
        "chat_messages": [],
    }

    finish_game_room = AsyncMock(return_value=room)
    clear_active_game = AsyncMock()
    clear_disconnected_at = AsyncMock()
    delete_game_state = AsyncMock()
    update_status = AsyncMock()
    cancel_turn_timer = Mock()

    monkeypatch.setattr("app.game.sync_runtime.get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.finish_game_room",
        finish_game_room,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.delete_game_state",
        delete_game_state,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.cancel_turn_timer",
        cancel_turn_timer,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.update_status",
        update_status,
    )
    monkeypatch.setattr(runtime, "clear_active_game", clear_active_game)
    monkeypatch.setattr(runtime, "clear_disconnected_at", clear_disconnected_at)

    await runtime.finalize_finished_game(state)

    cancel_turn_timer.assert_called_once_with("game-1")
    finish_game_room.assert_awaited_once_with(room_id="room-1")
    delete_game_state.assert_awaited_once_with("game-1")
    assert "game:game-1:patchlog" in deleted_keys
    assert "user:1:game" in deleted_keys
    assert "user:2:game" in deleted_keys
    clear_active_game.assert_any_await(user_id=1)
    clear_active_game.assert_any_await(user_id=2)
    clear_disconnected_at.assert_any_await(game_id="game-1", player_id=1)
    clear_disconnected_at.assert_any_await(game_id="game-1", player_id=2)
    update_status.assert_awaited_once_with(user_id="1", status="in_room")
    assert sio.emit.await_args_list[0].args == (
        "lobby_updated",
        {
            "action": "status_changed",
            "room": {
                "id": "room-1",
                "title": "테스트 방",
                "status": "waiting",
                "current_players": 2,
                "max_players": 4,
                "is_private": False,
                "host_id": "1",
                "host_nickname": "host",
            },
        },
    )
    assert sio.emit.await_args_list[1].args == (
        "room_updated",
        {
            "room_id": "room-1",
            "title": "테스트 방",
            "status": "waiting",
            "max_players": 4,
            "is_private": False,
            "players": room["players"],
            "chat_messages": [],
        },
    )
    assert sio.emit.await_args_list[1].kwargs == {"room": "room:room-1"}


@pytest.mark.asyncio
async def test_finalize_finished_game_cleans_up_room_when_nobody_connected(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    state = make_state()

    deleted_keys: list[str] = []

    class FakeRedis:
        async def delete(self, *keys: str) -> int:
            deleted_keys.extend(keys)
            return len(keys)

    room = {
        "id": "room-1",
        "title": "테스트 방",
        "status": "waiting",
        "max_players": 4,
        "is_private": False,
        "host_user_id": "1",
        "game_id": None,
        "players": [
            {"id": "1", "nickname": "host", "is_host": True, "is_ready": False},
            {"id": "2", "nickname": "guest", "is_host": False, "is_ready": False},
        ],
        "chat_messages": [],
    }

    finish_game_room = AsyncMock(return_value=room)
    cleanup_abandoned_room = AsyncMock()
    clear_active_game = AsyncMock()
    clear_disconnected_at = AsyncMock()
    delete_game_state = AsyncMock()
    update_status = AsyncMock()
    cancel_turn_timer = Mock()

    monkeypatch.setattr("app.game.sync_runtime.get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.finish_game_room",
        finish_game_room,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.cleanup_abandoned_room",
        cleanup_abandoned_room,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.delete_game_state",
        delete_game_state,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.cancel_turn_timer",
        cancel_turn_timer,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.update_status",
        update_status,
    )
    monkeypatch.setattr(runtime, "clear_active_game", clear_active_game)
    monkeypatch.setattr(runtime, "clear_disconnected_at", clear_disconnected_at)

    await runtime.finalize_finished_game(state)

    finish_game_room.assert_awaited_once_with(room_id="room-1")
    cleanup_abandoned_room.assert_awaited_once_with(
        room_id="room-1",
        player_ids=[1, 2],
    )
    delete_game_state.assert_awaited_once_with("game-1")
    assert "game:game-1:patchlog" in deleted_keys
    assert "user:1:game" in deleted_keys
    assert "user:2:game" in deleted_keys
    update_status.assert_not_awaited()
    assert sio.emit.await_args_list[0].args == (
        "lobby_updated",
        {"action": "removed", "room": {"id": "room-1"}},
    )
