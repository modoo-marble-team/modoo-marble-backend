from __future__ import annotations

import json
from pathlib import Path
import random
from typing import Any, Optional
from uuid import uuid4

from services.game.dice import Dice
from services.game.player import INITIAL_BALANCE, Player
from services.game.tile import Tile


DEFAULT_PASS_START_SALARY = 50_000_000
DEFAULT_MAX_PLAYERS = 4


class GameManager:
    def __init__(self, data_path: Optional[str] = None):
        self.data_path = (
            Path(data_path)
            if data_path
            else Path(__file__).resolve().parent / "data.json"
        )
        self.config = self._load_config()
        self.sessions: dict[str, GameSession] = {}

    def _load_config(self) -> dict[str, Any]:
        with self.data_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def make_player_via_id(self, user_id: str) -> Player:
        return Player(user_id=user_id)

    def create_session(
        self,
        player_ids: Optional[list[str]] = None,
        session_id: Optional[str] = None,
        dice: Optional[Dice] = None,
    ) -> "GameSession":
        new_session_id = session_id or str(uuid4())
        if new_session_id in self.sessions:
            raise ValueError(f"Session '{new_session_id}' already exists.")

        default_dice_values = self.config.get("dices", {}).get("default", [1, 2, 3, 4, 5, 6])
        session = GameSession(
            session_id=new_session_id,
            config=self.config,
            dice=dice or Dice(list(default_dice_values)),
        )
        for player_id in player_ids or []:
            session.register_player(self.make_player_via_id(player_id))
        self.sessions[new_session_id] = session
        return session

    def get_session(self, session_id: str) -> "GameSession":
        if session_id not in self.sessions:
            raise KeyError(f"Session '{session_id}' was not found.")
        return self.sessions[session_id]

    def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def get_game_result(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        return session.get_result()


class GameSession:
    def __init__(self, session_id: str, config: dict[str, Any], dice: Dice):
        self.session_id = session_id
        self.config = config
        self.dice = dice
        self.max_players = int(config.get("max_players", DEFAULT_MAX_PLAYERS))
        self.initial_balance = int(config.get("initial_balance", INITIAL_BALANCE))
        self.pass_start_salary = int(
            config.get("pass_start_salary", DEFAULT_PASS_START_SALARY)
        )

        self.players: list[Player] = []
        self.current_turn_index = 0
        self.turn_rolled = False
        self.last_roll: Optional[int] = None
        self.gameover = False
        self.winner_id: Optional[str] = None

        self.board = self._build_board(config)

    @property
    def board_size(self) -> int:
        return len(self.board)

    @property
    def active_players(self) -> list[Player]:
        return [player for player in self.players if not player.is_gameover]

    @property
    def current_player(self) -> Player:
        if not self.players:
            raise ValueError("No players in session.")
        self._skip_bankrupt_players()
        return self.players[self.current_turn_index]

    def register_player(self, player: Player) -> None:
        if len(self.players) >= self.max_players:
            raise ValueError("Session is full.")
        if any(existing.user_id == player.user_id for existing in self.players):
            raise ValueError(f"Player '{player.user_id}' is already registered.")
        player.balance = self.initial_balance
        player.position = 0
        player.is_gameover = False
        player.owned_tiles.clear()
        self.players.append(player)

    def roll_current_player(self, forced_value: Optional[int] = None) -> dict[str, Any]:
        self._assert_playable()
        if self.turn_rolled:
            raise ValueError("Current player already rolled. End turn first.")

        player = self.current_player
        roll_value = forced_value if forced_value is not None else self.dice.roll()
        if roll_value not in self.dice.values:
            raise ValueError("Forced dice value is not in dice faces.")

        passed_start = player.move(roll_value, self.board_size)
        if passed_start and not player.is_gameover:
            player.earn(self.pass_start_salary)

        self.turn_rolled = True
        self.last_roll = roll_value

        landed = self.board[player.position]
        event = {
            "type": "move",
            "player_id": player.user_id,
            "roll": roll_value,
            "position": player.position,
            "passed_start": passed_start,
            "landed_tile_name": None if landed is None else landed.name,
            "action": "none",
        }

        if landed is None:
            event["action"] = "start"
            return event

        if landed.owner_id is None:
            event["action"] = "buyable"
            return event

        if landed.owner_id == player.user_id:
            event["action"] = "upgradable"
            return event

        owner = self._find_player(landed.owner_id)
        paid = self._handle_toll_payment(
            payer=player,
            receiver=owner,
            amount=landed.current_toll,
        )
        event["action"] = "toll_paid" if paid else "bankrupt"
        event["toll"] = landed.current_toll
        self._refresh_game_result()
        return event

    def buy_current_tile(self) -> dict[str, Any]:
        if not self.turn_rolled:
            raise ValueError("Roll first.")
        player = self.current_player
        tile = self._get_current_tile(player)
        if tile is None:
            raise ValueError("Cannot buy START tile.")
        if tile.owner_id is not None:
            raise ValueError("Tile is already owned.")
        if not player.pay(tile.purchase_price):
            raise ValueError("Not enough balance to buy this tile.")
        tile.owner_id = player.user_id
        player.own_tile(tile.name)
        return {
            "type": "buy",
            "player_id": player.user_id,
            "tile_name": tile.name,
            "cost": tile.purchase_price,
        }

    def build_current_tile(self) -> dict[str, Any]:
        if not self.turn_rolled:
            raise ValueError("Roll first.")
        player = self.current_player
        tile = self._get_current_tile(player)
        if tile is None:
            raise ValueError("Cannot build on START tile.")
        if tile.owner_id != player.user_id:
            raise ValueError("Only owner can build on this tile.")

        cost = tile.next_build_cost()
        if cost is None:
            raise ValueError("Landmark is already built.")
        if not player.pay(cost):
            raise ValueError("Not enough balance to build.")

        tile.upgrade(player.user_id)
        return {
            "type": "build",
            "player_id": player.user_id,
            "tile_name": tile.name,
            "building_stage": tile.building_stage,
            "cost": cost,
        }

    def sell_tile_asset(self, tile_name: str) -> dict[str, Any]:
        player = self.current_player
        tile = self._get_tile_by_name(tile_name)
        if tile.owner_id != player.user_id:
            raise ValueError("Only owner can sell this tile.")

        if tile.building_stage > 0:
            refund = tile.sell_top_building()
            player.earn(refund)
            return {
                "type": "sell_building",
                "player_id": player.user_id,
                "tile_name": tile.name,
                "building_stage": tile.building_stage,
                "refund": refund,
            }

        refund = tile.sell_tile()
        player.release_tile(tile.name)
        player.earn(refund)
        return {
            "type": "sell_tile",
            "player_id": player.user_id,
            "tile_name": tile.name,
            "refund": refund,
        }

    def sell_next_asset(self) -> Optional[dict[str, Any]]:
        player = self.current_player
        owned_tiles = self._tiles_owned_by(player.user_id)
        if not owned_tiles:
            return None

        for tile in owned_tiles:
            if tile.building_stage > 0:
                return self.sell_tile_asset(tile.name)

        return self.sell_tile_asset(owned_tiles[0].name)

    def end_turn(self) -> dict[str, Any]:
        self._assert_playable()
        self.turn_rolled = False
        self.last_roll = None
        self._move_turn_to_next_alive_player()
        self._refresh_game_result()
        return {
            "type": "end_turn",
            "next_player_id": None if self.gameover else self.current_player.user_id,
        }

    def snapshot(self) -> dict[str, Any]:
        players = []
        for player in self.players:
            players.append(
                {
                    "user_id": player.user_id,
                    "balance": player.balance,
                    "position": player.position,
                    "is_gameover": player.is_gameover,
                    "owned_tiles": sorted(player.owned_tiles),
                }
            )

        board = []
        for index, tile in enumerate(self.board):
            if tile is None:
                board.append({"index": index, "name": "START"})
            else:
                board.append(
                    {
                        "index": index,
                        "name": tile.name,
                        "tier": tile.tier,
                        "owner_id": tile.owner_id,
                        "purchase_price": tile.purchase_price,
                        "base_toll": tile.base_toll,
                        "building_stage": tile.building_stage,
                        "building_label": tile.building_label,
                        "current_toll": tile.current_toll,
                    }
                )

        return {
            "session_id": self.session_id,
            "gameover": self.gameover,
            "winner_id": self.winner_id,
            "current_player_id": None if self.gameover else self.current_player.user_id,
            "turn_rolled": self.turn_rolled,
            "last_roll": self.last_roll,
            "players": players,
            "board": board,
        }

    def get_result(self) -> dict[str, Any]:
        ranking = sorted(
            self.players,
            key=lambda player: (not player.is_gameover, player.balance),
            reverse=True,
        )
        return {
            "session_id": self.session_id,
            "winner_id": self.winner_id,
            "ranking": [
                {
                    "rank": index + 1,
                    "player_id": player.user_id,
                    "balance": player.balance,
                    "is_gameover": player.is_gameover,
                }
                for index, player in enumerate(ranking)
            ],
            "state": self.snapshot(),
        }

    def _build_board(self, config: dict[str, Any]) -> list[Optional[Tile]]:
        tiles = config["tiles"]["korea"]
        purchase_values = config.get("purchase_values") or config.get("values")
        base_tolls = config.get("base_tolls")
        if base_tolls is None:
            base_tolls = {
                tier: int(value) // 10 for tier, value in purchase_values.items()
            }

        board: list[Optional[Tile]] = [None]
        tile_items = list(tiles.items())
        random.shuffle(tile_items)

        for tile_name, tier in tile_items:
            tier_key = str(tier)
            board.append(
                Tile(
                    name=tile_name,
                    tier=tier,
                    purchase_price=int(purchase_values[tier_key]),
                    base_toll=int(base_tolls[tier_key]),
                )
            )
        return board

    def _get_current_tile(self, player: Player) -> Optional[Tile]:
        tile = self.board[player.position]
        return tile

    def _get_tile_by_name(self, tile_name: str) -> Tile:
        for tile in self.board:
            if tile is not None and tile.name == tile_name:
                return tile
        raise ValueError(f"Unknown tile: {tile_name}")

    def _find_player(self, user_id: str) -> Player:
        for player in self.players:
            if player.user_id == user_id:
                return player
        raise ValueError(f"Player '{user_id}' not found.")

    def _tiles_owned_by(self, user_id: str) -> list[Tile]:
        return [
            tile
            for tile in self.board
            if tile is not None and tile.owner_id == user_id
        ]

    def _handle_toll_payment(self, payer: Player, receiver: Player, amount: int) -> bool:
        if payer.pay(amount):
            receiver.earn(amount)
            return True

        self._auto_liquidate_until(payer, amount)
        if payer.pay(amount):
            receiver.earn(amount)
            return True

        if payer.balance > 0:
            receiver.earn(payer.force_pay_all())
        self._bankrupt_player(payer)
        return False

    def _auto_liquidate_until(self, player: Player, required_amount: int) -> None:
        if player.balance >= required_amount:
            return

        while player.balance < required_amount:
            progress = False
            owned_tiles = self._tiles_owned_by(player.user_id)

            for tile in owned_tiles:
                while tile.building_stage > 0 and player.balance < required_amount:
                    player.earn(tile.sell_top_building())
                    progress = True

            for tile in list(owned_tiles):
                if tile.building_stage == 0 and player.balance < required_amount:
                    player.earn(tile.sell_tile())
                    player.release_tile(tile.name)
                    progress = True

            if not progress:
                break

    def _bankrupt_player(self, player: Player) -> None:
        for tile in self._tiles_owned_by(player.user_id):
            tile.owner_id = None
            tile.building_stage = 0
        player.bankrupt()

    def _skip_bankrupt_players(self) -> None:
        if not self.players:
            return
        for _ in range(len(self.players)):
            player = self.players[self.current_turn_index]
            if not player.is_gameover:
                return
            self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
        self.gameover = True
        self.winner_id = None

    def _move_turn_to_next_alive_player(self) -> None:
        if not self.players:
            return
        for _ in range(len(self.players)):
            self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
            if not self.players[self.current_turn_index].is_gameover:
                return
        self.gameover = True
        self.winner_id = None

    def _refresh_game_result(self) -> None:
        alive_players = self.active_players
        if len(alive_players) <= 1:
            self.gameover = True
            self.winner_id = alive_players[0].user_id if alive_players else None

    def _assert_playable(self) -> None:
        if not self.players:
            raise ValueError("No players in session.")
        if len(self.active_players) <= 1:
            self._refresh_game_result()
        if self.gameover:
            raise ValueError("Game is already over.")
    
