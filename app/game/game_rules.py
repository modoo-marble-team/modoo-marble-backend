"""룰셋 JSON에서 읽은 값을 코드용 상수로 꺼내 두는 모듈.

게임 로직은 이 값을 통해 현재 룰셋을 참조한다.
"""

from __future__ import annotations

from app.game.infrastructure.ruleset_loader import load_ruleset

RULESET = load_ruleset()
GAME_RULESET_VERSION = RULESET.version
INITIAL_BALANCE = RULESET.initial_balance
START_SALARY = RULESET.start_salary
MAX_ROUNDS = RULESET.max_rounds
MAX_BUILDING_LEVEL = RULESET.max_building_level
ISLAND_TILE_ID = RULESET.island_tile_id
ISLAND_LOCK_TURNS = 2
PROMPT_TIMEOUT_SECONDS = RULESET.prompt_timeout_seconds
TURN_TIMEOUT_SECONDS = RULESET.turn_timeout_seconds
BUILDING_STAGE_LABELS = dict(RULESET.building_stage_labels)
SELL_PURCHASE_PRICE_REFUND_RATIO = RULESET.sell_refund.purchase_price_ratio
SELL_BUILD_COST_REFUND_RATIO = RULESET.sell_refund.build_cost_ratio
ACQUISITION_PRICE_MULTIPLIER = RULESET.acquisition.multiplier
CHANCE_CARD_POOL = [card.to_dict() for card in RULESET.chance_cards]
EVENT_CARD_POOL = [card.to_dict() for card in RULESET.event_cards]
