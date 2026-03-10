from __future__ import annotations

from dataclasses import dataclass, field

from app.game.enums import TileType


@dataclass(frozen=True)
class TileDefinition:
    """보드 타일 하나의 불변 정보 (설계도)"""

    tile_id: int
    name: str
    tile_type: TileType
    tier: int = 0
    price: int = 0
    tolls: list[int] = field(default_factory=list)  # 레벨 0~7 통행료
    build_costs: list[int] = field(default_factory=list)  # 레벨 i→i+1 건설비


# 등급별 구매가 (만 단위)
_TIER_PRICE = {1: 500, 2: 1000, 3: 1500, 4: 2000, 5: 3000}

# tolls[i] = 건물 레벨 i일 때 통행료
_TIER_TOLLS = {
    1: [0, 50, 100, 150, 200, 300, 400, 600],
    2: [0, 100, 200, 300, 400, 600, 800, 1200],
    3: [0, 150, 300, 450, 600, 900, 1200, 1800],
    4: [0, 200, 400, 600, 800, 1200, 1600, 2400],
    5: [0, 300, 600, 900, 1200, 1800, 2400, 3600],
}

# build_costs[i] = 레벨 i에서 i+1로 올릴 때 드는 비용
# 인덱스 0 = 최초 토지 구매비 (레벨 0→1)
_TIER_BUILD_COSTS = {
    1: [500, 150, 150, 150, 200, 200, 200, 300],
    2: [1000, 250, 250, 250, 350, 350, 350, 500],
    3: [1500, 350, 350, 350, 500, 500, 500, 700],
    4: [2000, 450, 450, 450, 650, 650, 650, 900],
    5: [3000, 600, 600, 600, 900, 900, 900, 1200],
}


def _prop(tile_id: int, name: str, tier: int) -> TileDefinition:
    """PROPERTY 타일 생성 헬퍼"""
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
    """특수 칸 생성 헬퍼"""
    return TileDefinition(tile_id=tile_id, name=name, tile_type=tile_type)


BOARD: list[TileDefinition] = [
    _special(0, "출발", TileType.START),
    _prop(1, "제주도", 1),
    _special(2, "이벤트", TileType.EVENT),
    _prop(3, "부산", 1),
    _special(4, "찬스", TileType.CHANCE),
    _prop(5, "대구", 2),
    _special(6, "이벤트", TileType.EVENT),
    _prop(7, "인천", 2),
    _special(8, "무인도행", TileType.MOVE_TO_ISLAND),
    _prop(9, "광주", 2),
    _special(10, "이벤트", TileType.EVENT),
    _prop(11, "대전", 3),
    _special(12, "찬스", TileType.CHANCE),
    _prop(13, "울산", 3),
    _special(14, "이벤트", TileType.EVENT),
    _prop(15, "세종", 3),
    _special(16, "무인도", TileType.ISLAND),
    _prop(17, "강원", 3),
    _special(18, "이벤트", TileType.EVENT),
    _prop(19, "경기", 4),
    _special(20, "찬스", TileType.CHANCE),
    _prop(21, "충북", 4),
    _special(22, "이벤트", TileType.EVENT),
    _prop(23, "충남", 4),
    _special(24, "무인도행", TileType.MOVE_TO_ISLAND),
    _prop(25, "전북", 4),
    _special(26, "이벤트", TileType.EVENT),
    _prop(27, "전남", 5),
    _special(28, "찬스", TileType.CHANCE),
    _prop(29, "경북", 5),
    _special(30, "이벤트", TileType.EVENT),
    _prop(31, "경남", 5),
]

TILE_MAP: dict[int, TileDefinition] = {t.tile_id: t for t in BOARD}
BOARD_SIZE = len(BOARD)  # 32
ISLAND_TILE_ID = 16
START_TILE_ID = 0
START_SALARY = 200  # 출발점 통과 급여 (200만)
