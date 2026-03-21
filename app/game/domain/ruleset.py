from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game.enums import TileType


@dataclass(frozen=True, slots=True)
class CardDefinition:
    type: str
    amount: int
    description: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CardDefinition":
        return cls(
            type=str(data["type"]),
            amount=int(data.get("amount", 0)),
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "amount": self.amount,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class PropertyTierDefinition:
    tier: int
    price: int
    tolls: tuple[int, ...]
    build_costs: tuple[int, ...]

    @classmethod
    def from_dict(
        cls, tier: int, data: dict[str, Any]
    ) -> "PropertyTierDefinition":
        price = int(data["price"])
        return cls(
            tier=tier,
            price=price,
            tolls=tuple(int(value) for value in data.get("tolls", [])),
            build_costs=tuple(
                _normalize_build_costs(
                    price=price,
                    build_costs=data.get("build_costs", []),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class SellRefundDefinition:
    purchase_price_ratio: float
    build_cost_ratio: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SellRefundDefinition":
        return cls(
            purchase_price_ratio=float(data.get("purchase_price", 1.0)),
            build_cost_ratio=float(data.get("build_cost", 0.5)),
        )


@dataclass(frozen=True, slots=True)
class AcquisitionDefinition:
    multiplier: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AcquisitionDefinition":
        return cls(multiplier=float(data.get("multiplier", 1.0)))


@dataclass(frozen=True, slots=True)
class TileDefinition:
    tile_id: int
    name: str
    tile_type: TileType
    tier: int = 0
    price: int = 0
    tolls: tuple[int, ...] = field(default_factory=tuple)
    build_costs: tuple[int, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TileDefinition":
        return cls(
            tile_id=int(data["tile_id"]),
            name=str(data["name"]),
            tile_type=TileType(str(data["tile_type"])),
            tier=int(data.get("tier", 0)),
            price=int(data.get("price", 0)),
            tolls=tuple(int(value) for value in data.get("tolls", [])),
            build_costs=tuple(int(value) for value in data.get("build_costs", [])),
        )


@dataclass(frozen=True, slots=True)
class RuleSet:
    version: str
    initial_balance: int
    start_salary: int
    max_rounds: int
    max_building_level: int
    island_tile_id: int
    prompt_timeout_seconds: int
    turn_timeout_seconds: int
    building_stage_labels: dict[int, str]
    sell_refund: SellRefundDefinition
    acquisition: AcquisitionDefinition
    property_tiers: dict[int, PropertyTierDefinition]
    board: tuple[TileDefinition, ...]
    chance_cards: tuple[CardDefinition, ...]
    event_cards: tuple[CardDefinition, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleSet":
        property_tiers = {
            int(key): PropertyTierDefinition.from_dict(int(key), tier_data)
            for key, tier_data in dict(data.get("property_tiers", {})).items()
        }
        return cls(
            version=str(data["version"]),
            initial_balance=int(data["initial_balance"]),
            start_salary=int(data["start_salary"]),
            max_rounds=int(data["max_rounds"]),
            max_building_level=int(data["max_building_level"]),
            island_tile_id=int(data["island_tile_id"]),
            prompt_timeout_seconds=int(data["prompt_timeout_seconds"]),
            turn_timeout_seconds=int(data["turn_timeout_seconds"]),
            building_stage_labels={
                int(key): str(value)
                for key, value in dict(data.get("building_stage_labels", {})).items()
            },
            sell_refund=SellRefundDefinition.from_dict(
                dict(data.get("sell_refund", {}))
            ),
            acquisition=AcquisitionDefinition.from_dict(
                dict(data.get("acquisition", {}))
            ),
            property_tiers=property_tiers,
            board=tuple(
                cls._resolve_tile_definition(tile_data, property_tiers)
                for tile_data in data.get("board", [])
            ),
            chance_cards=tuple(
                CardDefinition.from_dict(card_data)
                for card_data in data.get("chance_cards", [])
            ),
            event_cards=tuple(
                CardDefinition.from_dict(card_data)
                for card_data in data.get("event_cards", [])
            ),
        )

    @staticmethod
    def _resolve_tile_definition(
        tile_data: dict[str, Any],
        property_tiers: dict[int, PropertyTierDefinition],
    ) -> TileDefinition:
        tile = TileDefinition.from_dict(tile_data)
        if tile.tile_type != TileType.PROPERTY:
            return tile

        tier_config = property_tiers.get(tile.tier)
        if tier_config is None:
            if tile.price > 0 and tile.tolls and tile.build_costs:
                return TileDefinition(
                    tile_id=tile.tile_id,
                    name=tile.name,
                    tile_type=tile.tile_type,
                    tier=tile.tier,
                    price=tile.price,
                    tolls=tile.tolls,
                    build_costs=_normalize_build_costs(
                        price=tile.price,
                        build_costs=tile.build_costs,
                    ),
                )
            raise ValueError(
                f"Missing property tier config for tile {tile.tile_id} (tier {tile.tier})"
            )

        return TileDefinition(
            tile_id=tile.tile_id,
            name=tile.name,
            tile_type=tile.tile_type,
            tier=tile.tier,
            price=int(tile_data.get("price", tier_config.price)),
            tolls=tuple(
                int(value) for value in tile_data.get("tolls", tier_config.tolls)
            ),
            build_costs=tuple(
                _normalize_build_costs(
                    price=int(tile_data.get("price", tier_config.price)),
                    build_costs=tile_data.get("build_costs", tier_config.build_costs),
                )
            ),
        )

    @property
    def tile_map(self) -> dict[int, TileDefinition]:
        return {tile.tile_id: tile for tile in self.board}

    @property
    def board_size(self) -> int:
        return len(self.board)


def _normalize_build_costs(
    *,
    price: int,
    build_costs: Any,
) -> tuple[int, ...]:
    normalized = tuple(int(value) for value in build_costs)
    if normalized and normalized[0] == price:
        return normalized[1:]
    return normalized
