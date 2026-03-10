from __future__ import annotations

import json
from contextlib import asynccontextmanager

from app.game.board import BOARD, TileType
from app.game.enums import PlayerState
from app.game.schemas import GameState, PlayerGameState, TileGameState
from app.redis_client import get_redis

GAME_STATE_TTL = 86400  # Redis 자동 삭제 시간: 24시간 (초 단위)


def _game_key(game_id: str) -> str:
    """게임 상태를 저장할 Redis 키"""
    return f"game:{game_id}:state"


GAME_LOCK_TIMEOUT = 5


@asynccontextmanager
async def game_lock(game_id: str):
    """
    게임 상태를 수정할 때 반드시 이 안에서 실행.
    한 번에 한 요청만 처리되도록 자물쇠 역할을 한다.
    """
    redis = get_redis()
    lock = redis.lock(
        f"game:{game_id}:lock",
        timeout=GAME_LOCK_TIMEOUT,
    )
    async with lock:
        yield


def _make_initial_players(
    player_ids: list[int],
    nicknames: dict[int, str],
) -> dict[str, PlayerGameState]:
    """플레이어 목록으로 초기 상태 딕셔너리를 만든다."""
    players: dict[str, PlayerGameState] = {}
    for order, uid in enumerate(player_ids):
        players[str(uid)] = PlayerGameState(
            user_id=uid,
            nickname=nicknames.get(uid, "Unknown"),
            balance=100,
            current_tile_id=0,
            state=PlayerState.NORMAL,
            state_duration=0,
            consecutive_doubles=0,
            owned_tile_ids=[],
            building_levels={},
            turn_order=order,
        )
    return players


def _make_initial_tiles() -> dict[str, TileGameState]:
    """PROPERTY 타일만 골라서 초기 상태 딕셔너리를 만든다."""
    tiles: dict[str, TileGameState] = {}
    for tile in BOARD:
        if tile.tile_type == TileType.PROPERTY:
            tiles[str(tile.tile_id)] = TileGameState(
                owner_id=None,
                building_level=0,
            )
    return tiles


async def init_game_state(
    game_id: str,
    room_id: str,
    player_ids: list[int],
    nicknames: dict[int, str],
) -> GameState:
    """
    게임 시작 시 초기 상태를 만들어 Redis에 저장한다.
    player_ids의 순서가 곧 턴 순서.
    """
    state = GameState(
        game_id=game_id,
        room_id=room_id,
        revision=0,
        turn=1,
        round=1,
        current_player_id=player_ids[0],
        status="playing",
        players=_make_initial_players(player_ids, nicknames),
        tiles=_make_initial_tiles(),
    )
    redis = get_redis()
    await redis.set(_game_key(game_id), json.dumps(state), ex=GAME_STATE_TTL)
    return state


async def get_game_state(game_id: str) -> GameState | None:
    """Redis에서 게임 상태를 읽어온다. 없으면 None."""
    redis = get_redis()
    raw = await redis.get(_game_key(game_id))
    if raw is None:
        return None
    return json.loads(raw)


async def save_game_state(game_id: str, state: GameState) -> None:
    """수정된 게임 상태를 Redis에 다시 저장한다."""
    redis = get_redis()
    await redis.set(_game_key(game_id), json.dumps(state), ex=GAME_STATE_TTL)


async def delete_game_state(game_id: str) -> None:
    """게임 종료 시 Redis에서 상태를 삭제한다. (Unit 10에서 호출)"""
    redis = get_redis()
    await redis.delete(_game_key(game_id))


def get_tile_state(state: GameState, tile_id: int) -> TileGameState | None:
    """
    타일 상태를 안전하게 가져온다.
    EVENT, CHANCE 같은 특수 칸은 None을 반환한다.
    """
    return state["tiles"].get(str(tile_id))
