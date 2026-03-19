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


_TIER_PRICE = {1: 300, 2: 500, 3: 700, 4: 1000, 5: 1400}
_TIER_TOLLS = {
    1: [40, 70, 120, 190, 280, 400, 560, 780],
    2: [60, 110, 190, 300, 430, 620, 860, 1180],
    3: [90, 150, 260, 410, 600, 860, 1180, 1600],
    4: [120, 210, 360, 560, 820, 1160, 1600, 2160],
    5: [160, 280, 470, 730, 1060, 1500, 2060, 2760],
}
_TIER_BUILD_COSTS = {
    1: [300, 180, 220, 280, 380, 500, 650, 850],
    2: [500, 260, 320, 400, 520, 680, 860, 1080],
    3: [700, 360, 450, 560, 720, 920, 1150, 1450],
    4: [1000, 500, 620, 780, 980, 1230, 1530, 1880],
    5: [1400, 650, 800, 1000, 1250, 1550, 1900, 2300],
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
START_SALARY = 500
