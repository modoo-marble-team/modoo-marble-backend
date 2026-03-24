from __future__ import annotations

from typing import Any, TypedDict

from app.game.models import (
    GameState,
)


class ServerPatchOperation(TypedDict):
    op: str
    path: str
    value: Any


class GamePatch(TypedDict):
    game_id: str
    revision: int
    turn: int
    round: int
    events: list[dict[str, Any]]
    patch: list[ServerPatchOperation]
    snapshot: GameState | None
