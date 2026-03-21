from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.game.models import GameState
from app.game.state import game_lock, get_game_state, save_game_state


class GameStateRepository:
    @asynccontextmanager
    async def lock(self, game_id: str) -> AsyncIterator[None]:
        async with game_lock(game_id):
            yield

    async def load(self, game_id: str) -> GameState | None:
        return await get_game_state(game_id)

    async def save(self, game_id: str, state: GameState) -> None:
        await save_game_state(game_id, state)
