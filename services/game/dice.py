from __future__ import annotations

from dataclasses import dataclass, field
import random


@dataclass(slots=True)
class Dice:
    values: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5, 6])

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("Dice must have at least one face.")

    def roll(self) -> int:
        return random.choice(self.values)


def get_default_dice() -> Dice:
    return Dice([1, 2, 3, 4, 5, 6])
