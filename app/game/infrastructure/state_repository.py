"""게임 상태 저장소의 얇은 어댑터.

애플리케이션 계층이 Redis 세부 구현을 직접 모르도록 감싼다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.game.models import GameState
from app.game.state import game_lock, get_game_state, save_game_state


class GameStateRepository:
    @asynccontextmanager
    async def lock(self, game_id: str) -> AsyncIterator[None]:
        # 상태 수정 전에 게임 단위 잠금을 잡는다.
        async with game_lock(game_id):
            yield

    async def load(self, game_id: str) -> GameState | None:
        # 저장소에서 GameState를 읽는다.
        return await get_game_state(game_id)

    async def save(self, game_id: str, state: GameState) -> None:
        # 변경된 GameState를 저장소에 저장한다.
        await save_game_state(game_id, state)
