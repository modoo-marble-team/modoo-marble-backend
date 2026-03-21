from app.game.domain.ruleset import RuleSet
from app.game.enums import TileType
from app.game.infrastructure.ruleset_loader import load_ruleset


def test_default_ruleset_applies_property_tier_values():
    ruleset = load_ruleset()

    suwon = ruleset.tile_map[1]
    seoul = ruleset.tile_map[31]
    tier_1 = ruleset.property_tiers[1]
    tier_5 = ruleset.property_tiers[5]

    assert suwon.tile_type == TileType.PROPERTY
    assert suwon.tier == 1
    assert suwon.price == tier_1.price
    assert suwon.tolls == tier_1.tolls
    assert suwon.build_costs == tier_1.build_costs

    assert seoul.tile_type == TileType.PROPERTY
    assert seoul.tier == 5
    assert seoul.price == tier_5.price
    assert seoul.tolls == tier_5.tolls
    assert seoul.build_costs == tier_5.build_costs
    assert ruleset.sell_refund.purchase_price_ratio == 0.9
    assert ruleset.sell_refund.build_cost_ratio == 0.75
    assert ruleset.acquisition.multiplier == 1.5


def test_ruleset_allows_per_tile_override_on_top_of_tier_defaults():
    ruleset = RuleSet.from_dict(
        {
            "version": "test.v1",
            "initial_balance": 500000,
            "start_salary": 70000,
            "max_rounds": 20,
            "max_building_level": 3,
            "island_tile_id": 8,
            "prompt_timeout_seconds": 30,
            "turn_timeout_seconds": 30,
            "building_stage_labels": {"1": "별장", "2": "호텔", "3": "랜드마크"},
            "property_tiers": {
                "2": {
                    "price": 50000,
                    "tolls": [10000, 20000, 90000, 155000],
                    "build_costs": [60000, 155000, 180000],
                }
            },
            "board": [
                {
                    "tile_id": 11,
                    "name": "포항",
                    "tile_type": "PROPERTY",
                    "tier": 2,
                    "price": 55000,
                    "tolls": [11000, 22000, 99000, 160000],
                }
            ],
            "chance_cards": [],
            "event_cards": [],
        }
    )

    tile = ruleset.tile_map[11]

    assert tile.price == 55000
    assert tile.tolls == (11000, 22000, 99000, 160000)
    assert tile.build_costs == (60000, 155000, 180000)


def test_ruleset_normalizes_legacy_build_costs_that_repeat_price():
    ruleset = RuleSet.from_dict(
        {
            "version": "test.v1",
            "initial_balance": 500000,
            "start_salary": 70000,
            "max_rounds": 20,
            "max_building_level": 3,
            "island_tile_id": 8,
            "prompt_timeout_seconds": 30,
            "turn_timeout_seconds": 30,
            "building_stage_labels": {"1": "별장", "2": "호텔", "3": "랜드마크"},
            "property_tiers": {
                "2": {
                    "price": 50000,
                    "tolls": [10000, 20000, 90000, 155000],
                    "build_costs": [50000, 60000, 155000, 180000],
                }
            },
            "board": [
                {
                    "tile_id": 11,
                    "name": "포항",
                    "tile_type": "PROPERTY",
                    "tier": 2,
                }
            ],
            "chance_cards": [],
            "event_cards": [],
        }
    )

    assert ruleset.tile_map[11].build_costs == (60000, 155000, 180000)


def test_ruleset_requires_property_tier_when_property_values_are_missing():
    try:
        RuleSet.from_dict(
            {
                "version": "test.v1",
                "initial_balance": 500000,
                "start_salary": 70000,
                "max_rounds": 20,
                "max_building_level": 3,
                "island_tile_id": 8,
                "prompt_timeout_seconds": 30,
                "turn_timeout_seconds": 30,
                "building_stage_labels": {
                    "1": "별장",
                    "2": "호텔",
                    "3": "랜드마크",
                },
                "board": [
                    {
                        "tile_id": 11,
                        "name": "포항",
                        "tile_type": "PROPERTY",
                        "tier": 2,
                    }
                ],
                "chance_cards": [],
                "event_cards": [],
            }
        )
    except ValueError as exc:
        assert "Missing property tier config" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing property tier config")
