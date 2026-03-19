from __future__ import annotations

from dataclasses import dataclass, field

from app.game.enums import TileType


@dataclass(frozen=True)
class TileDefinition:
    tile_id: int
    name: str
    tile_type: TileType
    tier: int = 0
    price: int = 0
    tolls: list[int] = field(default_factory=list)
    build_costs: list[int] = field(default_factory=list)


_TIER_PRICE = {1: 500, 2: 1000, 3: 1500, 4: 2000, 5: 3000}
_TIER_TOLLS = {
    1: [0, 50, 100, 150, 200, 300, 400, 600],
    2: [0, 100, 200, 300, 400, 600, 800, 1200],
    3: [0, 150, 300, 450, 600, 900, 1200, 1800],
    4: [0, 200, 400, 600, 800, 1200, 1600, 2400],
    5: [0, 300, 600, 900, 1200, 1800, 2400, 3600],
}
_TIER_BUILD_COSTS = {
    1: [500, 150, 150, 150, 200, 200, 200, 300],
    2: [1000, 250, 250, 250, 350, 350, 350, 500],
    3: [1500, 350, 350, 350, 500, 500, 500, 700],
    4: [2000, 450, 450, 450, 650, 650, 650, 900],
    5: [3000, 600, 600, 600, 900, 900, 900, 1200],
}


def _prop(tile_id: int, name: str, tier: int) -> TileDefinition:
    return TileDefinition(
        tile_id=tile_id,
        name=name,
        tile_type=TileType.PROPERTY,
        tier=tier,
        price=_TIER_PRICE[tier],
        tolls=_TIER_TOLLS[tier],
        build_costs=_TIER_BUILD_COSTS[tier],
    )


def _special(tile_id: int, name: str, tile_type: TileType) -> TileDefinition:
    return TileDefinition(tile_id=tile_id, name=name, tile_type=tile_type)


BOARD: list[TileDefinition] = [
    _special(0, "출발", TileType.START),
    _prop(1, "수원", 1),
    _prop(2, "용인", 1),
    _special(3, "찬스", TileType.CHANCE),
    _prop(4, "군산", 1),
    _prop(5, "평택", 2),
    _prop(6, "익산", 2),
    _special(7, "이벤트", TileType.EVENT),
    _special(8, "무인도", TileType.ISLAND),
    _prop(9, "경주", 2),
    _special(10, "찬스", TileType.CHANCE),
    _prop(11, "포항", 3),
    _prop(12, "대구", 3),
    _prop(13, "청원", 3),
    _prop(14, "울산", 3),
    _prop(15, "부산", 3),
    _special(16, "여행", TileType.TRAVEL),
    _prop(17, "제주", 4),
    _prop(18, "여수", 4),
    _prop(19, "광주", 4),
    _special(20, "찬스", TileType.CHANCE),
    _prop(21, "춘천", 4),
    _prop(22, "강릉", 4),
    _prop(23, "원주", 4),
    _special(24, "섬으로 이동", TileType.MOVE_TO_ISLAND),
    _prop(25, "청주", 5),
    _prop(26, "천안", 5),
    _special(27, "찬스", TileType.CHANCE),
    _prop(28, "대전", 5),
    _prop(29, "인천", 5),
    _special(30, "이벤트", TileType.EVENT),
    _prop(31, "서울", 5),
]

TILE_MAP: dict[int, TileDefinition] = {tile.tile_id: tile for tile in BOARD}
BOARD_SIZE = len(BOARD)
ISLAND_TILE_ID = 8
START_TILE_ID = 0
START_SALARY = 200
