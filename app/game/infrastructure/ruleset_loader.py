from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.game.domain.ruleset import RuleSet

RULESET_DIR = Path(__file__).resolve().parent.parent / "rulesets"


@lru_cache(maxsize=8)
def load_ruleset(version: str = "default.v1") -> RuleSet:
    path = RULESET_DIR / f"{version}.json"
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return RuleSet.from_dict(payload)
