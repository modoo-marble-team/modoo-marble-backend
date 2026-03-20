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


_TIER_PRICE = {
    1: 300_00,
    2: 500_00,
    3: 700_00,
    4: 1100_00,
    5: 1700_00,
}

_TIER_TOLLS = {
    1: [50_00, 100_00, 300_00, 650_00],
    2: [100_00, 200_00, 900_00, 1550_00],
    3: [150_00, 300_00, 950_00, 1750_00],
    4: [180_00, 350_00, 950_00, 1550_00],
    5: [200_00, 400_00, 900_00, 1300_00],
}

_TIER_BUILD_COSTS = {
    1: [300_00, 400_00, 850_00, 1400_00],
    2: [500_00, 600_00, 1550_00, 1800_00],
    3: [700_00, 800_00, 1900_00, 2500_00],
    4: [1100_00, 1600_00, 3400_00, 3900_00],
    5: [1700_00, 2800_00, 5300_00, 5600_00],
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
    _prop(5, "평택", 1),
    _prop(6, "익산", 1),
    _special(7, "이벤트", TileType.EVENT),
    _special(8, "무인도", TileType.ISLAND),
    _prop(9, "경주", 1),
    _special(10, "찬스", TileType.CHANCE),
    _prop(11, "포항", 2),
    _prop(12, "대구", 3),
    _prop(13, "청원", 2),
    _prop(14, "울산", 3),
    _prop(15, "부산", 5),
    _special(16, "여행", TileType.TRAVEL),
    _prop(17, "제주", 5),
    _prop(18, "여수", 3),
    _prop(19, "광주", 3),
    _special(20, "이벤트", TileType.EVENT),
    _prop(21, "춘천", 2),
    _prop(22, "강릉", 4),
    _prop(23, "원주", 2),
    _special(24, "섬으로 이동", TileType.MOVE_TO_ISLAND),
    _prop(25, "청주", 2),
    _prop(26, "천안", 3),
    _special(27, "찬스", TileType.CHANCE),
    _prop(28, "대전", 4),
    _prop(29, "인천", 4),
    _special(30, "이벤트", TileType.EVENT),
    _prop(31, "서울", 5),
]

TILE_MAP: dict[int, TileDefinition] = {tile.tile_id: tile for tile in BOARD}
BOARD_SIZE = len(BOARD)
ISLAND_TILE_ID = 8
START_TILE_ID = 0
START_SALARY = 5000_00
