from __future__ import annotations

from dataclasses import dataclass, field


INITIAL_BALANCE = 1_000_000_000


@dataclass(slots=True)
class Player:
    user_id: str
    balance: int = INITIAL_BALANCE
    position: int = 0
    is_gameover: bool = False
    owned_tiles: set[str] = field(default_factory=set)

    def earn(self, amount: int) -> None:
        if self.is_gameover or amount <= 0:
            return
        self.balance += int(amount)

    def pay(self, amount: int) -> bool:
        if self.is_gameover:
            return False
        amount = int(amount)
        if amount <= 0:
            return True
        if amount > self.balance:
            return False
        self.balance -= amount
        return True

    def force_pay_all(self) -> int:
        amount = self.balance
        self.balance = 0
        return amount

    def move(self, steps: int, board_size: int) -> bool:
        if self.is_gameover:
            return False
        if steps < 0:
            raise ValueError("Steps must be >= 0.")
        origin = self.position
        new_position = (origin + steps) % board_size
        passed_start = origin + steps >= board_size
        self.position = new_position
        return passed_start

    def bankrupt(self) -> None:
        self.is_gameover = True
        self.balance = 0
        self.owned_tiles.clear()

    def own_tile(self, tile_name: str) -> None:
        self.owned_tiles.add(tile_name)

    def release_tile(self, tile_name: str) -> None:
        self.owned_tiles.discard(tile_name)
