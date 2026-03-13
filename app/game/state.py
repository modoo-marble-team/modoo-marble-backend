from __future__ import annotations

import json
from contextlib import asynccontextmanager

from app.game.board import BOARD, TileType
from app.game.enums import PlayerState
from app.game.schemas import GameState, PlayerGameState, TileGameState
from app.redis_client import get_redis

GAME_STATE_TTL = 86400
GAME_LOCK_TIMEOUT = 5
INITIAL_BALANCE = 5000


def _game_key(game_id: str) -> str:
    return f"game:{game_id}:state"


class LockAcquisitionError(Exception):
    pass


@asynccontextmanager
async def game_lock(game_id: str):
    redis = get_redis()
    lock = redis.lock(
        f"game:{game_id}:lock",
        timeout=GAME_LOCK_TIMEOUT,
    )
    try:
        acquired = await lock.acquire(blocking=True, blocking_timeout=3)
        if not acquired:
            raise LockAcquisitionError(f"game {game_id} lock acquisition failed")
        yield
    finally:
        try:
            await lock.release()
        except Exception:
            pass


def _make_initial_players(
    player_ids: list[int],
    nicknames: dict[int, str],
) -> dict[str, PlayerGameState]:
    players: dict[str, PlayerGameState] = {}
    for order, uid in enumerate(player_ids):
        players[str(uid)] = PlayerGameState(
            playerId=uid,
            nickname=nicknames.get(uid, "Unknown"),
            balance=INITIAL_BALANCE,
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
    tiles: dict[str, TileGameState] = {}
    for tile in BOARD:
        if tile.tile_type == TileType.PROPERTY:
            tiles[str(tile.tile_id)] = TileGameState(
                ownerId=None,
                buildingLevel=0,
            )
    return tiles


async def init_game_state(
    game_id: str,
    room_id: str,
    player_ids: list[int],
    nicknames: dict[int, str],
) -> GameState:
    state = GameState(
        game_id=game_id,
        room_id=room_id,
        revision=0,
        turn=1,
        round=1,
        current_player_id=player_ids[0],
        status="playing",
        phase="WAIT_ROLL",
        players=_make_initial_players(player_ids, nicknames),
        tiles=_make_initial_tiles(),
        pending_prompt=None,
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
    redis = get_redis()
    await redis.delete(_game_key(game_id))


def get_tile_state(state: GameState, tile_id: int) -> TileGameState | None:
    return state["tiles"].get(str(tile_id))


def get_player_state(state: GameState, user_id: int) -> PlayerGameState | None:
    return state["players"].get(str(user_id))


def apply_patches(state: GameState, patches: list[dict]) -> None:
    for patch in patches:
        op = patch["op"]
        path = patch["path"]
        value = patch["value"]

        keys = path.split(".")
        target = state
        for key in keys[:-1]:
            target = target[key]  # type: ignore[index]

        last_key = keys[-1]

        if op == "set":
            target[last_key] = value  # type: ignore[index]
        elif op == "inc":
            target[last_key] = target[last_key] + value  # type: ignore[index]
        elif op == "push":
            target[last_key].append(value)  # type: ignore[index]
        elif op == "remove":
            if isinstance(target[last_key], list):  # type: ignore[index]
                target[last_key].remove(value)  # type: ignore[index]
            else:
                del target[last_key]  # type: ignore[index]

