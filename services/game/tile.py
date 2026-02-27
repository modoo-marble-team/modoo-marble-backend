from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


TOLL_MULTIPLIERS: dict[int, int] = {
    0: 1,
    1: 2,
    2: 3,
    3: 5,
    4: 7,
    5: 10,
}

BUILD_COST_MULTIPLIERS: dict[int, float] = {
    1: 0.5,
    2: 0.5,
    3: 0.5,
    4: 1.0,
    5: 1.5,
}

BUILDING_LABELS: dict[int, str] = {
    0: "EMPTY",
    1: "HOUSE_1",
    2: "HOUSE_2",
    3: "HOUSE_3",
    4: "HOTEL",
    5: "LANDMARK",
}

TILE_REFUND_RATIO = 0.5
BUILDING_REFUND_RATIO = 0.5
MAX_BUILDING_STAGE = 5


@dataclass(slots=True)
class Tile:
    name: str
    tier: int
    purchase_price: int
    base_toll: int
    owner_id: Optional[str] = None
    building_stage: int = 0

    @property
    def is_owned(self) -> bool:
        return self.owner_id is not None

    @property
    def is_upgradable(self) -> bool:
        return self.building_stage < MAX_BUILDING_STAGE

    @property
    def building_label(self) -> str:
        return BUILDING_LABELS[self.building_stage]

    @property
    def current_toll(self) -> int:
        return int(self.base_toll * TOLL_MULTIPLIERS[self.building_stage])

    def next_build_cost(self) -> Optional[int]:
        if not self.is_upgradable:
            return None
        next_stage = self.building_stage + 1
        return int(self.purchase_price * BUILD_COST_MULTIPLIERS[next_stage])

    def upgrade(self, owner_id: str) -> int:
        if self.owner_id != owner_id:
            raise ValueError("타일 소유자만 건설할 수 있습니다.")
        cost = self.next_build_cost()
        if cost is None:
            raise ValueError("더 이상 건설할 수 없습니다.")
        self.building_stage += 1
        return cost

    def sell_top_building(self) -> int:
        if self.building_stage <= 0:
            raise ValueError("팔 건물이 없습니다.")
        ratio = BUILD_COST_MULTIPLIERS[self.building_stage]
        refund = int(self.purchase_price * ratio * BUILDING_REFUND_RATIO)
        self.building_stage -= 1
        return refund

    def sell_tile(self) -> int:
        if self.building_stage > 0:
            raise ValueError("타일은 건물을 전부 판 뒤 팔 수 있습니다.")
        refund = int(self.purchase_price * TILE_REFUND_RATIO)
        self.owner_id = None
        return refund
