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


def test_all_game_players_offline_only_when_no_active_sids():
    runtime = GameSyncRuntime(AsyncMock())
    state = make_state()

    assert runtime._all_game_players_offline(state) is True

    runtime._user_sids[1] = {"sid-1"}
    assert runtime._all_game_players_offline(state) is False


@pytest.mark.asyncio
async def test_cleanup_abandoned_game_room_clears_room_and_game_keys(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    state = make_state()

    deleted_keys: list[str] = []

    class FakeRedis:
        async def delete(self, *keys: str) -> int:
            deleted_keys.extend(keys)
            return len(keys)

    cleanup_room = AsyncMock()
    clear_active_game = AsyncMock()
    clear_disconnected_at = AsyncMock()
    delete_game_state = AsyncMock()
    cancel_turn_timer = Mock()

    monkeypatch.setattr(
        "app.game.sync_runtime.get_redis",
        lambda: FakeRedis(),
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.cleanup_abandoned_room",
        cleanup_room,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.delete_game_state",
        delete_game_state,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.cancel_turn_timer",
        cancel_turn_timer,
    )
    monkeypatch.setattr(runtime, "clear_active_game", clear_active_game)
    monkeypatch.setattr(runtime, "clear_disconnected_at", clear_disconnected_at)

    await runtime._cleanup_abandoned_game_room(state)

    cancel_turn_timer.assert_called_once_with("game-1")
    cleanup_room.assert_awaited_once_with(room_id="room-1", player_ids=[1, 2])
    delete_game_state.assert_awaited_once_with("game-1")
    assert "game:game-1:patchlog" in deleted_keys
    assert "user:1:game" in deleted_keys
    assert "user:2:game" in deleted_keys
    clear_active_game.assert_any_await(user_id=1)
    clear_active_game.assert_any_await(user_id=2)
    clear_disconnected_at.assert_any_await(game_id="game-1", player_id=1)
    clear_disconnected_at.assert_any_await(game_id="game-1", player_id=2)
    sio.emit.assert_awaited_once_with(
        "lobby_updated",
        {"action": "removed", "room": {"id": "room-1"}},
    )
