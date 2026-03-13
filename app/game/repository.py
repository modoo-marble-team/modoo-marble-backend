from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from redis.exceptions import WatchError

from app.redis_client import get_redis

logger = logging.getLogger(__name__)


class GameStateConflictError(RuntimeError):
    pass


class GameStateCorruptedError(RuntimeError):
    pass


class GameRepository:
    def __init__(
        self,
        save_retry_count: int = 5,
        read_retry_count: int = 5,
        backoff_base_ms: int = 20,
    ) -> None:
        self._save_retry_count = max(save_retry_count, 1)
        self._read_retry_count = max(read_retry_count, 1)
        self._backoff_base_ms = max(backoff_base_ms, 1)

    def _state_key(self, game_id: int) -> str:
        return f"game:{game_id}:state"

    def _turn_key(self, game_id: int) -> str:
        return f"game:{game_id}:turn"

    def _dump(self, value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False)

    async def _backoff(self, attempt: int) -> None:
        delay_ms = self._backoff_base_ms * (2**attempt)
        await asyncio.sleep(delay_ms / 1000)

    def _as_int(self, value: Any, default: int = 0) -> int:
        if value is None or value == "":
            return default
        return int(value)

    def _as_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "y", "yes"}:
                return True
            if lowered in {"false", "0", "n", "no"}:
                return False
        return bool(value)

    def _normalize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        state["gameId"] = self._as_int(state.get("gameId"))
        state["revision"] = self._as_int(state.get("revision"))
        state["currentRound"] = self._as_int(state.get("currentRound"), 1)
        state["maxRound"] = self._as_int(state.get("maxRound"), 20)

        for player in state.get("players", []):
            current_tile_id = player.get("currentTileId", player.get("position"))
            player["currentTileId"] = self._as_int(current_tile_id)
            player.pop("position", None)

            player["userId"] = self._as_int(player.get("userId"))
            player["balance"] = self._as_int(player.get("balance"))
            player["stateDuration"] = self._as_int(player.get("stateDuration"))
            player["isAlive"] = self._as_bool(player.get("isAlive"), True)
            player["ownedTiles"] = [
                self._as_int(tile_id) for tile_id in player.get("ownedTiles", [])
            ]

        for index, tile in enumerate(state.get("tiles", [])):
            tile["tileId"] = self._as_int(tile.get("tileId"), index)

            owner_user_id = tile.get("ownerUserId")
            tile["ownerUserId"] = (
                self._as_int(owner_user_id) if owner_user_id is not None else None
            )

            building_level = tile.get("buildingLevel", tile.get("buildingCount"))
            tile["buildingLevel"] = self._as_int(building_level)
            tile.pop("buildingCount", None)

        travel_destinations = state.get("travelDestinations")
        if isinstance(travel_destinations, list):
            state["travelDestinations"] = [
                self._as_int(destination) for destination in travel_destinations
            ]

        return state

    def _normalize_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
        turn["currentPlayerIndex"] = self._as_int(turn.get("currentPlayerIndex"))

        current_user_id = turn.get("currentUserId")
        turn["currentUserId"] = (
            self._as_int(current_user_id) if current_user_id is not None else None
        )

        turn["revision"] = self._as_int(turn.get("revision"))
        return turn

    async def load(self, *, game_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        redis = get_redis()
        state_key = self._state_key(game_id)
        turn_key = self._turn_key(game_id)

        for attempt in range(self._read_retry_count):
            raw_state = await redis.get(state_key)
            raw_turn = await redis.get(turn_key)

            if not raw_state:
                raise ValueError("Game state not found")
            if not raw_turn:
                raise ValueError("Game turn not found")

            state = self._normalize_state(json.loads(raw_state))
            turn = self._normalize_turn(json.loads(raw_turn))

            state_revision = int(state["revision"])
            turn_revision = int(turn.get("revision", state_revision))

            if state_revision == turn_revision:
                return state, turn

            logger.warning(
                "game revision mismatch detected",
                extra={
                    "game_id": game_id,
                    "state_revision": state_revision,
                    "turn_revision": turn_revision,
                    "attempt": attempt + 1,
                },
            )
            await self._backoff(attempt)

        raise GameStateCorruptedError("Game state read conflict")

    async def get_state(self, *, game_id: int) -> dict[str, Any]:
        state, _ = await self.load(game_id=game_id)
        return state

    async def get_turn(self, *, game_id: int) -> dict[str, Any]:
        _, turn = await self.load(game_id=game_id)
        return turn

    async def initialize(
        self,
        *,
        game_id: int,
        state: dict[str, Any],
        turn: dict[str, Any],
    ) -> None:
        redis = get_redis()
        state_key = self._state_key(game_id)
        turn_key = self._turn_key(game_id)

        normalized_state = self._normalize_state(state)
        normalized_turn = self._normalize_turn(turn)
        normalized_turn["revision"] = normalized_state["revision"]

        for attempt in range(self._save_retry_count):
            async with redis.pipeline() as pipe:
                try:
                    await pipe.watch(state_key, turn_key)

                    existing_state = await pipe.get(state_key)
                    existing_turn = await pipe.get(turn_key)
                    if existing_state or existing_turn:
                        raise ValueError("Game already initialized")

                    pipe.multi()
                    pipe.set(state_key, self._dump(normalized_state))
                    pipe.set(turn_key, self._dump(normalized_turn))
                    await pipe.execute()
                    return
                except WatchError:
                    logger.warning(
                        "game initialize watch conflict",
                        extra={"game_id": game_id, "attempt": attempt + 1},
                    )
                    if attempt == self._save_retry_count - 1:
                        break
                    await self._backoff(attempt)

        raise GameStateConflictError("Game initialization conflict")

    async def save(
        self,
        *,
        game_id: int,
        state: dict[str, Any],
        turn: dict[str, Any],
        expected_revision: int,
    ) -> None:
        redis = get_redis()
        state_key = self._state_key(game_id)
        turn_key = self._turn_key(game_id)

        normalized_state = self._normalize_state(state)
        normalized_turn = self._normalize_turn(turn)
        normalized_turn["revision"] = normalized_state["revision"]

        for attempt in range(self._save_retry_count):
            async with redis.pipeline() as pipe:
                try:
                    await pipe.watch(state_key, turn_key)

                    raw_state = await pipe.get(state_key)
                    raw_turn = await pipe.get(turn_key)

                    if not raw_state:
                        raise ValueError("Game state not found")
                    if not raw_turn:
                        raise ValueError("Game turn not found")

                    current_state = self._normalize_state(json.loads(raw_state))
                    current_turn = self._normalize_turn(json.loads(raw_turn))

                    current_revision = int(current_state["revision"])
                    current_turn_revision = int(
                        current_turn.get("revision", current_revision)
                    )

                    if current_revision != int(expected_revision):
                        raise GameStateConflictError("Game state version conflict")
                    if current_turn_revision != int(expected_revision):
                        raise GameStateConflictError("Game turn version conflict")

                    pipe.multi()
                    pipe.set(state_key, self._dump(normalized_state))
                    pipe.set(turn_key, self._dump(normalized_turn))
                    await pipe.execute()
                    return
                except WatchError:
                    logger.warning(
                        "game save watch conflict",
                        extra={
                            "game_id": game_id,
                            "expected_revision": expected_revision,
                            "attempt": attempt + 1,
                        },
                    )
                    if attempt == self._save_retry_count - 1:
                        break
                    await self._backoff(attempt)

        raise GameStateConflictError("Game state version conflict")
