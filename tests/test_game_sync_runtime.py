from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from app.game.board import TILE_MAP
from app.game.enums import PlayerState
from app.game.models import GameState, PlayerGameState, TileGameState
from app.game.state import INITIAL_BALANCE
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
                balance=INITIAL_BALANCE + 30000,
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


def test_winner_payload_includes_total_assets():
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    state = make_state()
    state.tiles = {4: TileGameState(owner_id=1, building_level=1)}
    state.players[1].owned_tiles = [4]
    state.players[1].building_levels = {4: 1}
    tile = TILE_MAP[4]

    winner = runtime._winner_payload(state, state.players[1])

    assert winner["playerId"] == 1
    assert winner["balance"] == INITIAL_BALANCE + 30000
    assert (
        winner["assets"] == INITIAL_BALANCE + 30000 + tile.price + tile.build_costs[0]
    )


@pytest.mark.asyncio
async def test_get_active_game_falls_back_to_legacy_key(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)

    class FakeRedis:
        async def get(self, key: str):
            if key == "game:user:1:active":
                return None
            if key == "user:1:game":
                return "game-legacy"
            return None

    monkeypatch.setattr("app.game.sync_runtime.get_redis", lambda: FakeRedis())

    game_id = await runtime.get_active_game(user_id=1)

    assert game_id == "game-legacy"


@pytest.mark.asyncio
async def test_set_disconnected_at_keeps_tracking_key_longer_than_grace(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    captured: dict[str, int] = {}

    class FakeRedis:
        async def set(self, key: str, value: str, ex: int | None = None):
            captured["ex"] = int(ex or 0)
            return True

        async def zadd(self, key: str, mapping: dict[str, float]):
            return 1

    monkeypatch.setattr("app.game.sync_runtime.get_redis", lambda: FakeRedis())

    await runtime.set_disconnected_at(game_id="game-1", player_id=1)

    assert captured["ex"] > 60


@pytest.mark.asyncio
async def test_remove_player_from_room_updates_lobby_count_and_host(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)

    room = {
        "id": "room-1",
        "title": "테스트 방",
        "status": "playing",
        "max_players": 4,
        "is_private": False,
        "host_user_id": "1",
        "game_id": "game-1",
        "players": [
            {"id": "1", "nickname": "host", "is_host": True, "is_ready": False},
            {"id": "2", "nickname": "guest", "is_host": False, "is_ready": False},
        ],
        "chat_messages": [],
    }
    updated_room = deepcopy(room)
    updated_room["host_user_id"] = "2"
    updated_room["players"] = [
        {"id": "2", "nickname": "guest", "is_host": True, "is_ready": False},
    ]

    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.get_room",
        AsyncMock(return_value=deepcopy(room)),
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.leave_room",
        AsyncMock(return_value=(updated_room, "2")),
    )

    await runtime._remove_player_from_room(room_id="room-1", player_id=1)

    emitted = sio.emit.await_args_list
    assert any(
        call.args[0] == "lobby_updated"
        and call.args[1]["action"] == "updated"
        and call.args[1]["room"]["current_players"] == 1
        for call in emitted
    )
    assert any(
        call.args[0] == "host_changed" and call.args[1]["new_host_id"] == "2"
        for call in emitted
    )


@pytest.mark.asyncio
async def test_reconcile_playing_rooms_on_startup_marks_active_players_disconnected(
    monkeypatch,
):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    state = GameState(
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
                balance=0,
                current_tile_id=0,
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

    class FakeRedis:
        async def smembers(self, key: str):
            assert key == "rooms:index"
            return {"room-1"}

        async def srem(self, key: str, value: str):
            return 1

    monkeypatch.setattr("app.game.sync_runtime.get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.get_room",
        AsyncMock(
            return_value={
                "id": "room-1",
                "status": "playing",
                "game_id": "game-1",
                "players": [
                    {"id": "1", "nickname": "host", "is_host": True, "is_ready": False},
                    {
                        "id": "2",
                        "nickname": "guest",
                        "is_host": False,
                        "is_ready": False,
                    },
                ],
            }
        ),
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.get_game_state",
        AsyncMock(return_value=state),
    )
    monkeypatch.setattr(
        runtime,
        "get_disconnected_at",
        AsyncMock(return_value=None),
    )
    set_disconnected_at = AsyncMock()
    monkeypatch.setattr(runtime, "set_disconnected_at", set_disconnected_at)

    await runtime._reconcile_playing_rooms_on_startup()

    set_disconnected_at.assert_awaited_once_with(game_id="game-1", player_id=1)


@pytest.mark.asyncio
async def test_reconcile_playing_rooms_on_startup_cleans_stale_room(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    deleted_keys: list[str] = []

    class FakeRedis:
        async def smembers(self, key: str):
            assert key == "rooms:index"
            return {"room-1"}

        async def srem(self, key: str, value: str):
            return 1

        async def delete(self, *keys: str) -> int:
            deleted_keys.extend(keys)
            return len(keys)

    room = {
        "id": "room-1",
        "status": "playing",
        "game_id": "game-1",
        "players": [
            {"id": "1", "nickname": "host", "is_host": True, "is_ready": False},
            {"id": "2", "nickname": "guest", "is_host": False, "is_ready": False},
        ],
    }

    cleanup_abandoned_room = AsyncMock()
    clear_active_game = AsyncMock()
    clear_disconnected_at = AsyncMock()

    monkeypatch.setattr("app.game.sync_runtime.get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.get_room",
        AsyncMock(return_value=room),
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.RoomService.cleanup_abandoned_room",
        cleanup_abandoned_room,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.get_game_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.delete_game_state",
        AsyncMock(),
    )
    monkeypatch.setattr(runtime, "clear_active_game", clear_active_game)
    monkeypatch.setattr(runtime, "clear_disconnected_at", clear_disconnected_at)

    await runtime._reconcile_playing_rooms_on_startup()

    cleanup_abandoned_room.assert_awaited_once_with(room_id="room-1", player_ids=[1, 2])
    clear_active_game.assert_any_await(user_id=1)
    clear_active_game.assert_any_await(user_id=2)
    clear_disconnected_at.assert_any_await(game_id="game-1", player_id=1)
    clear_disconnected_at.assert_any_await(game_id="game-1", player_id=2)
    assert "game:game-1:patchlog" in deleted_keys
    assert "user:1:game" in deleted_keys
    assert "user:2:game" in deleted_keys
    assert sio.emit.await_args_list[-1].args == (
        "lobby_updated",
        {"action": "removed", "room": {"id": "room-1"}},
    )


@pytest.mark.asyncio
async def test_leave_game_immediately_bankrupts_and_cleans_membership(monkeypatch):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    runtime._user_sids[1] = {"sid-1"}
    state = GameState(
        game_id="game-1",
        room_id="room-1",
        revision=4,
        turn=2,
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
            3: PlayerGameState(
                player_id=3,
                nickname="third",
                balance=INITIAL_BALANCE,
                current_tile_id=0,
                player_state=PlayerState.NORMAL,
                state_duration=0,
                consecutive_doubles=0,
                owned_tiles=[],
                building_levels={},
                turn_order=2,
            ),
        },
        tiles={},
    )

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    save_game_state = AsyncMock()
    cleanup_membership = AsyncMock()
    finalize_finished_game = AsyncMock()

    monkeypatch.setattr("app.game.sync_runtime.game_lock", fake_game_lock)
    monkeypatch.setattr(
        "app.game.sync_runtime.get_game_state",
        AsyncMock(return_value=state),
    )
    monkeypatch.setattr("app.game.sync_runtime.save_game_state", save_game_state)
    monkeypatch.setattr(
        runtime,
        "build_and_store_patch_packet",
        AsyncMock(
            return_value={
                "gameId": "game-1",
                "revision": 5,
                "turn": 3,
                "events": [],
                "patch": [],
                "snapshot": None,
            }
        ),
    )
    monkeypatch.setattr(runtime, "_cleanup_player_game_membership", cleanup_membership)
    monkeypatch.setattr(runtime, "finalize_finished_game", finalize_finished_game)

    left = await runtime.leave_game(game_id="game-1", user_id=1)

    assert left is True
    assert state.revision == 5
    assert state.require_player(1).player_state == PlayerState.BANKRUPT
    assert state.current_player_id == 2
    save_game_state.assert_awaited_once_with("game-1", state)
    cleanup_membership.assert_awaited_once_with(
        game_id="game-1",
        room_id="room-1",
        player_id=1,
        presence_status="lobby",
        leave_socket_rooms=True,
    )
    finalize_finished_game.assert_not_awaited()
    assert any(
        call.args
        and call.args[0] == "game:patch"
        and call.kwargs == {"room": "game:game-1"}
        for call in sio.emit.await_args_list
    )


@pytest.mark.asyncio
async def test_leave_game_finishes_game_and_excludes_departed_player_from_finalize(
    monkeypatch,
):
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)
    runtime._user_sids[1] = {"sid-1"}
    state = GameState(
        game_id="game-1",
        room_id="room-1",
        revision=1,
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

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    monkeypatch.setattr("app.game.sync_runtime.game_lock", fake_game_lock)
    monkeypatch.setattr(
        "app.game.sync_runtime.get_game_state",
        AsyncMock(return_value=state),
    )
    monkeypatch.setattr("app.game.sync_runtime.save_game_state", AsyncMock())
    monkeypatch.setattr(
        runtime,
        "build_and_store_patch_packet",
        AsyncMock(
            return_value={
                "gameId": "game-1",
                "revision": 2,
                "turn": 1,
                "events": [],
                "patch": [],
                "snapshot": None,
            }
        ),
    )
    monkeypatch.setattr(runtime, "_cleanup_player_game_membership", AsyncMock())
    finalize_finished_game = AsyncMock()
    monkeypatch.setattr(runtime, "finalize_finished_game", finalize_finished_game)

    left = await runtime.leave_game(game_id="game-1", user_id=1)

    assert left is True
    finalize_finished_game.assert_awaited_once_with(
        state,
        excluded_player_ids={1},
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
    update_status_and_emit = AsyncMock()
    emit_online_users = AsyncMock()
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
        "app.game.sync_runtime.update_status_and_emit",
        update_status_and_emit,
    )
    monkeypatch.setattr(
        "app.game.sync_runtime.emit_online_users",
        emit_online_users,
    )
    monkeypatch.setattr(runtime, "clear_active_game", clear_active_game)
    monkeypatch.setattr(runtime, "clear_disconnected_at", clear_disconnected_at)
    monkeypatch.setattr(
        "app.game.sync_runtime.persist_game_result",
        AsyncMock(),
    )

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
    update_status_and_emit.assert_awaited_once_with(
        sio,
        user_id="1",
        status="in_room",
        nickname="host",
        emit_snapshot=False,
    )
    emit_online_users.assert_awaited_once_with(sio)
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
    update_status_and_emit = AsyncMock()
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
        "app.game.sync_runtime.update_status_and_emit",
        update_status_and_emit,
    )
    monkeypatch.setattr(runtime, "clear_active_game", clear_active_game)
    monkeypatch.setattr(runtime, "clear_disconnected_at", clear_disconnected_at)
    monkeypatch.setattr(
        "app.game.sync_runtime.persist_game_result",
        AsyncMock(),
    )

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
    update_status_and_emit.assert_not_awaited()
    assert sio.emit.await_args_list[0].args == (
        "lobby_updated",
        {"action": "removed", "room": {"id": "room-1"}},
    )


@pytest.mark.asyncio
async def test_leave_game_returns_true_when_game_already_finished(monkeypatch):
    """게임이 이미 종료(finished)된 상태에서 나가기 요청 시 멤버십을 정리하고 True를 반환해야 한다.

    GAME_OVER 이벤트 직후 finalize 완료 전에 플레이어가 나가기를 누르면
    state.status == "finished"이지만 room.status는 아직 "playing"일 수 있다.
    이 경우 기존에는 False를 반환해 "게임 나가기를 처리할 수 없습니다." 에러가 발생했다.
    """
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)

    state = GameState(
        game_id="game-1",
        room_id="room-1",
        revision=10,
        turn=5,
        round=3,
        current_player_id=2,
        status="finished",
        phase="GAME_OVER",
        pending_prompt=None,
        winner_id=2,
        players={
            1: PlayerGameState(
                player_id=1,
                nickname="host",
                balance=0,
                current_tile_id=0,
                player_state=PlayerState.BANKRUPT,
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

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    cleanup_membership = AsyncMock()

    monkeypatch.setattr("app.game.sync_runtime.game_lock", fake_game_lock)
    monkeypatch.setattr(
        "app.game.sync_runtime.get_game_state",
        AsyncMock(return_value=state),
    )
    monkeypatch.setattr(runtime, "_cleanup_player_game_membership", cleanup_membership)

    left = await runtime.leave_game(game_id="game-1", user_id=1)

    assert left is True
    cleanup_membership.assert_awaited_once_with(
        game_id="game-1",
        room_id="room-1",
        player_id=1,
        presence_status="lobby",
        leave_socket_rooms=True,
    )


@pytest.mark.asyncio
async def test_leave_game_returns_false_when_game_state_missing(monkeypatch):
    """Redis에 게임 상태가 없으면 False를 반환해야 한다."""
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    monkeypatch.setattr("app.game.sync_runtime.game_lock", fake_game_lock)
    monkeypatch.setattr(
        "app.game.sync_runtime.get_game_state",
        AsyncMock(return_value=None),
    )

    left = await runtime.leave_game(game_id="ghost-game", user_id=1)

    assert left is False


@pytest.mark.asyncio
async def test_leave_game_returns_false_when_player_not_in_finished_game(monkeypatch):
    """게임이 종료됐더라도 해당 플레이어가 게임에 없으면 False를 반환해야 한다."""
    sio = AsyncMock()
    runtime = GameSyncRuntime(sio)

    state = GameState(
        game_id="game-1",
        room_id="room-1",
        revision=10,
        turn=5,
        round=3,
        current_player_id=2,
        status="finished",
        phase="GAME_OVER",
        pending_prompt=None,
        winner_id=2,
        players={
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

    @asynccontextmanager
    async def fake_game_lock(_game_id: str):
        yield

    monkeypatch.setattr("app.game.sync_runtime.game_lock", fake_game_lock)
    monkeypatch.setattr(
        "app.game.sync_runtime.get_game_state",
        AsyncMock(return_value=state),
    )

    left = await runtime.leave_game(game_id="game-1", user_id=99)

    assert left is False
