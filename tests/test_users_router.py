from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.game.enums import PlayerState
from app.game.models import GameState, PlayerGameState
from app.game.state import INITIAL_BALANCE
from app.routers import users


class FakeRedis:
    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


def make_game_state() -> GameState:
    return GameState(
        game_id="game-1",
        room_id="room-1",
        revision=2,
        turn=3,
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
            )
        },
        tiles={},
    )


def test_get_me_returns_profile_and_stats(monkeypatch):
    auth = SimpleNamespace(user_id=1, is_guest=False)
    user = SimpleNamespace(
        id=1,
        nickname="홍길동",
        profile_image_url="https://example.com/profile.png",
        is_guest=False,
    )

    async def fake_get_me(*, user_id: int):
        assert user_id == 1
        return user

    async def fake_get_stats(*, user_id: int) -> dict[str, int]:
        assert user_id == 1
        return {
            "total_games": 12,
            "wins": 5,
            "losses": 7,
        }

    monkeypatch.setattr(users.users_service, "get_me", fake_get_me)
    monkeypatch.setattr(users.users_service, "get_stats", fake_get_stats)

    result = asyncio.run(users.get_me(auth))

    assert result.id == 1
    assert result.nickname == "홍길동"
    assert result.profile_image_url == "https://example.com/profile.png"
    assert result.is_guest is False
    assert result.stats.total_games == 12
    assert result.stats.wins == 5
    assert result.stats.losses == 7


def test_get_me_context_returns_waiting_room_context(monkeypatch):
    auth = SimpleNamespace(user_id=1, is_guest=False)

    async def fake_get_user_room_id(user_id: int) -> str | None:
        assert user_id == 1
        return "room-1"

    async def fake_get_room(room_id: str) -> dict | None:
        assert room_id == "room-1"
        return {
            "id": "room-1",
            "title": "테스트 방",
            "status": "waiting",
            "game_id": None,
        }

    async def fake_get_user_status(user_id: str) -> str | None:
        assert user_id == "1"
        return "in_room"

    monkeypatch.setattr(users.room_service, "_get_user_room_id", fake_get_user_room_id)
    monkeypatch.setattr(users.room_service, "get_room", fake_get_room)
    monkeypatch.setattr(users, "get_user_status", fake_get_user_status)
    monkeypatch.setattr(users, "get_redis", lambda: FakeRedis({}))

    result = asyncio.run(users.get_me_context(auth))

    assert result.room_id == "room-1"
    assert result.room_title == "테스트 방"
    assert result.room_status == "waiting"
    assert result.game_id is None
    assert result.presence_status == "in_room"
    assert result.resume_target == "room"


def test_get_me_context_returns_active_game_context(monkeypatch):
    auth = SimpleNamespace(user_id=1, is_guest=True)

    async def fake_get_user_room_id(user_id: int) -> str | None:
        assert user_id == 1
        return None

    async def fake_get_room(room_id: str) -> dict | None:
        assert room_id == "room-1"
        return {
            "id": "room-1",
            "title": "플레이 중 방",
            "status": "playing",
            "game_id": "game-1",
        }

    async def fake_get_user_status(user_id: str) -> str | None:
        assert user_id == "1"
        return "playing"

    async def fake_get_game_state(game_id: str) -> GameState | None:
        assert game_id == "game-1"
        return make_game_state()

    monkeypatch.setattr(users.room_service, "_get_user_room_id", fake_get_user_room_id)
    monkeypatch.setattr(users.room_service, "get_room", fake_get_room)
    monkeypatch.setattr(users, "get_user_status", fake_get_user_status)
    monkeypatch.setattr(
        users,
        "get_redis",
        lambda: FakeRedis({"game:user:1:active": "game-1"}),
    )
    monkeypatch.setattr(users, "get_game_state", fake_get_game_state)

    result = asyncio.run(users.get_me_context(auth))

    assert result.room_id == "room-1"
    assert result.room_title == "플레이 중 방"
    assert result.room_status == "playing"
    assert result.game_id == "game-1"
    assert result.presence_status == "playing"
    assert result.resume_target == "game"


def test_get_me_context_ignores_stale_game_in_room(monkeypatch):
    auth = SimpleNamespace(user_id=1, is_guest=False)

    async def fake_get_user_room_id(user_id: int) -> str | None:
        assert user_id == 1
        return "room-1"

    async def fake_get_room(room_id: str) -> dict | None:
        assert room_id == "room-1"
        return {
            "id": "room-1",
            "title": "테스트 방",
            "status": "waiting",
            "game_id": "ghost-game",
        }

    async def fake_get_user_status(user_id: str) -> str | None:
        assert user_id == "1"
        return "in_room"

    async def fake_get_game_state(game_id: str) -> GameState | None:
        assert game_id == "ghost-game"
        return None

    monkeypatch.setattr(users.room_service, "_get_user_room_id", fake_get_user_room_id)
    monkeypatch.setattr(users.room_service, "get_room", fake_get_room)
    monkeypatch.setattr(users, "get_user_status", fake_get_user_status)
    monkeypatch.setattr(users, "get_redis", lambda: FakeRedis({}))
    monkeypatch.setattr(users, "get_game_state", fake_get_game_state)

    result = asyncio.run(users.get_me_context(auth))

    assert result.room_id == "room-1"
    assert result.room_title == "테스트 방"
    assert result.room_status == "waiting"
    assert result.game_id is None
    assert result.presence_status == "in_room"
    assert result.resume_target == "room"
