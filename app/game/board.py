from __future__ import annotations

from app.game.domain.ruleset import TileDefinition
from app.game.game_rules import ISLAND_TILE_ID, RULESET, START_SALARY

BOARD: list[TileDefinition] = list(RULESET.board)
TILE_MAP: dict[int, TileDefinition] = RULESET.tile_map
BOARD_SIZE = RULESET.board_size

__all__ = [
    "BOARD",
    "BOARD_SIZE",
    "ISLAND_TILE_ID",
    "RULESET",
    "START_SALARY",
    "TILE_MAP",
    "TileDefinition",
]
