"""게임 상태를 만들고, 저장하고, 복원하는 모듈.

여기서는 규칙 계산보다 '상태를 어떻게 보관할지'에 집중한다.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import is_dataclass
from typing import Any

from app.game.board import BOARD
from app.game.enums import PlayerState, TileType
from app.game.game_rules import INITIAL_BALANCE
from app.game.models import GameState, PlayerGameState, TileGameState
from app.redis_client import get_redis

GAME_STATE_TTL = 86400
GAME_LOCK_TIMEOUT = 5


def _game_key(game_id: str) -> str:
    return f"game:{game_id}:state"


class LockAcquisitionError(Exception):
    pass


@asynccontextmanager
async def game_lock(game_id: str):
    # 같은 게임 상태를 동시에 두 요청이 수정하지 않도록 잠금을 건다.
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
) -> dict[int, PlayerGameState]:
    # 새 게임 시작 시 플레이어 기본 상태를 만든다.
    players: dict[int, PlayerGameState] = {}
    for order, uid in enumerate(player_ids):
        players[uid] = PlayerGameState(
            player_id=uid,
            nickname=nicknames.get(uid, "Unknown"),
            balance=INITIAL_BALANCE,
            current_tile_id=0,
            player_state=PlayerState.NORMAL,
            state_duration=0,
            consecutive_doubles=0,
            owned_tiles=[],
            building_levels={},
            turn_order=order,
            extra_turn_effect_turns_remaining=0,
            extra_turn_effect_active=False,
        )
    return players


def _make_initial_tiles() -> dict[int, TileGameState]:
    # 소유권이 생길 수 있는 PROPERTY 타일만 상태 테이블에 넣는다.
    tiles: dict[int, TileGameState] = {}
    for tile in BOARD:
        if tile.tile_type == TileType.PROPERTY:
            tiles[tile.tile_id] = TileGameState(
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
    # 게임 시작 시점의 GameState를 만들고 바로 저장한다.
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
        winner_id=None,
    )
    redis = get_redis()
    await redis.set(_game_key(game_id), json.dumps(state.to_json()), ex=GAME_STATE_TTL)
    return state


async def get_game_state(game_id: str) -> GameState | None:
    # Redis에 저장된 문자열을 읽어서 GameState로 복원한다.
    redis = get_redis()
    raw = await redis.get(_game_key(game_id))
    if raw is None:
        return None
    return GameState.from_json(json.loads(raw))


async def save_game_state(game_id: str, state: GameState) -> None:
    # 현재 GameState를 Redis에 저장한다.
    redis = get_redis()
    await redis.set(_game_key(game_id), json.dumps(state.to_json()), ex=GAME_STATE_TTL)


async def delete_game_state(game_id: str) -> None:
    redis = get_redis()
    await redis.delete(_game_key(game_id))


def get_tile_state(state: GameState, tile_id: int) -> TileGameState | None:
    return state.tile(tile_id)


def get_player_state(state: GameState, user_id: int) -> PlayerGameState | None:
    return state.player(user_id)


def _normalize_path_segment(segment: str) -> str:
    if segment.isdigit():
        return segment
    return re.sub(r"(?<!^)(?=[A-Z])", "_", segment).lower()


def _coerce_mapping_key(target: dict[Any, Any], key: str) -> Any:
    if key in target:
        return key
    if key.isdigit():
        numeric_key = int(key)
        if numeric_key in target or not target:
            return numeric_key
    return key


def _get_child(target: Any, key: str) -> Any:
    normalized = _normalize_path_segment(key)

    if is_dataclass(target):
        return getattr(target, normalized)
    if isinstance(target, dict):
        return target[_coerce_mapping_key(target, normalized)]
    if isinstance(target, list):
        return target[int(normalized)]

    raise TypeError(f"Unsupported patch target: {type(target)!r}")


def _set_child(target: Any, key: str, value: Any) -> None:
    normalized = _normalize_path_segment(key)

    if is_dataclass(target):
        setattr(target, normalized, value)
        return
    if isinstance(target, dict):
        target[_coerce_mapping_key(target, normalized)] = value
        return
    if isinstance(target, list):
        target[int(normalized)] = value
        return

    raise TypeError(f"Unsupported patch target: {type(target)!r}")


def apply_patches(state: GameState, patches: list[dict]) -> None:
    # 서버에서 만든 patch 목록을 실제 상태 객체에 적용한다.
    for patch in patches:
        op = patch["op"]
        path = patch["path"]
        value = patch["value"]

        keys = path.split(".")
        target: Any = state
        for key in keys[:-1]:
            target = _get_child(target, key)

        last_key = keys[-1]

        if op == "set":
            _set_child(target, last_key, value)
            continue

        current_value = _get_child(target, last_key)

        if op == "inc":
            _set_child(target, last_key, current_value + value)
        elif op == "push":
            current_value.append(value)
        elif op == "remove":
            if isinstance(current_value, list):
                current_value.remove(value)
            elif isinstance(current_value, dict):
                current_value.pop(_coerce_mapping_key(current_value, str(value)), None)
            else:
                raise TypeError(f"Remove is unsupported for target: {path}")
