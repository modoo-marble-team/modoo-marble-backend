from __future__ import annotations

import random
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from app.game.board import TILE_MAP
from app.game.enums import (
    GameOverReason,
    MoveTrigger,
    PlayerState,
    ServerEventType,
    TileType,
)
from app.game.patch import make_patch, op_set
from app.game.repository import GameRepository


class VictoryConditionStrategy(Protocol):
    def evaluate(
        self,
        *,
        state: dict[str, Any],
        alive_players: list[dict[str, Any]],
    ) -> dict[str, Any] | None: ...


class LastSurvivorVictoryCondition:
    def evaluate(
        self,
        *,
        state: dict[str, Any],
        alive_players: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if len(alive_players) != 1:
            return None

        winner_user_id = alive_players[0]["userId"]
        state["status"] = "FINISHED"
        state["winnerUserId"] = winner_user_id

        return {
            "type": ServerEventType.GAME_OVER.value,
            "reason": GameOverReason.LAST_SURVIVOR.value,
            "winnerUserId": winner_user_id,
        }


def _legacy_player_total_assets(
    *, state: dict[str, Any], player: dict[str, Any]
) -> int:
    player_id = int(player["userId"])
    total_assets = int(player.get("balance", 0))

    for tile in state.get("tiles", []):
        owner_user_id = tile.get("ownerUserId")
        if owner_user_id is None or int(owner_user_id) != player_id:
            continue

        tile_id = int(tile.get("tileId", -1))
        tile_def = TILE_MAP.get(tile_id)
        if tile_def is None:
            continue

        building_level = int(tile.get("buildingLevel", 0))
        total_assets += tile_def.price + sum(tile_def.build_costs[:building_level])

    return total_assets


class MaxRoundVictoryCondition:
    def evaluate(
        self,
        *,
        state: dict[str, Any],
        alive_players: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        current_round = int(state.get("currentRound", 1))
        max_round = int(state.get("maxRound", 20))
        if current_round <= max_round:
            return None

        state["currentRound"] = max_round

        winner_user_id = None
        if alive_players:
            winner = max(
                alive_players,
                key=lambda player: (
                    _legacy_player_total_assets(state=state, player=player),
                    int(player["balance"]),
                ),
            )
            winner_user_id = winner["userId"]

        state["status"] = "FINISHED"
        state["winnerUserId"] = winner_user_id

        return {
            "type": ServerEventType.GAME_OVER.value,
            "reason": GameOverReason.MAX_ROUND_REACHED.value,
            "winnerUserId": winner_user_id,
        }


class GameService:
    def __init__(
        self,
        repository: GameRepository | None = None,
        dice_roller: Callable[[], tuple[int, int]] | None = None,
        victory_conditions: list[VictoryConditionStrategy] | None = None,
        immutable_travel_destinations: list[int] | None = None,
    ) -> None:
        self.repository = repository or GameRepository()

        if dice_roller is not None:
            self._dice_roller = dice_roller
        else:
            rng = random.SystemRandom()
            self._dice_roller = lambda: (rng.randint(1, 6), rng.randint(1, 6))

        self._victory_conditions = victory_conditions or [
            LastSurvivorVictoryCondition(),
            MaxRoundVictoryCondition(),
        ]
        self._immutable_travel_destinations = (
            {int(destination) for destination in immutable_travel_destinations}
            if immutable_travel_destinations
            else set()
        )

    async def initialize_game(
        self,
        *,
        game_id: int,
        player_ids: list[int],
        start_cash: int = 500000,
        max_round: int = 20,
        tiles: list[dict[str, Any]] | None = None,
        travel_destinations: list[int] | None = None,
    ) -> dict[str, Any]:
        if len(player_ids) < 2:
            raise ValueError("At least 2 players are required")

        players: list[dict[str, Any]] = []
        for player_id in player_ids:
            players.append(
                {
                    "userId": int(player_id),
                    "currentTileId": 0,
                    "balance": int(start_cash),
                    "playerState": PlayerState.NORMAL.value,
                    "stateDuration": 0,
                    "ownedTiles": [],
                    "isAlive": True,
                    "bankruptAt": None,
                }
            )

        state = {
            "gameId": int(game_id),
            "status": "IN_PROGRESS",
            "maxRound": int(max_round),
            "currentRound": 1,
            "winnerUserId": None,
            "players": players,
            "tiles": self._prepare_tiles(tiles or []),
            "travelDestinations": [
                int(destination) for destination in travel_destinations
            ]
            if travel_destinations
            else [],
            "revision": 0,
            "createdAt": datetime.now(UTC).isoformat(),
        }
        turn = {
            "currentPlayerIndex": 0,
            "currentUserId": int(player_ids[0]),
            "revision": 0,
        }

        await self.repository.initialize(game_id=game_id, state=state, turn=turn)

        return make_patch(
            game_id=game_id,
            revision=state["revision"],
            turn=int(turn["currentPlayerIndex"]),
            events=[],
            patch=[],
            snapshot=deepcopy(state),
        )

    async def apply_bankruptcy(
        self,
        *,
        game_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        state, turn = await self.repository.load(game_id=game_id)
        expected_revision = int(state["revision"])

        self._ensure_not_finished(state=state)

        player_index = self._find_player_index(state=state, user_id=user_id)
        player = state["players"][player_index]

        if player["playerState"] == PlayerState.BANKRUPT.value:
            return make_patch(
                game_id=game_id,
                revision=state["revision"],
                turn=int(turn["currentPlayerIndex"]),
                events=[],
                patch=[],
            )

        events: list[dict[str, Any]] = []
        patch: list[dict[str, Any]] = []

        player["playerState"] = PlayerState.BANKRUPT.value
        player["isAlive"] = False
        player["stateDuration"] = 0
        player["bankruptAt"] = datetime.now(UTC).isoformat()

        patch.append(
            op_set(
                self._player_path(player_index, "playerState"),
                PlayerState.BANKRUPT.value,
            )
        )
        patch.append(op_set(self._player_path(player_index, "isAlive"), False))
        patch.append(op_set(self._player_path(player_index, "stateDuration"), 0))
        patch.append(
            op_set(self._player_path(player_index, "bankruptAt"), player["bankruptAt"])
        )

        returned_tile_ids = self._release_player_tiles(state=state, user_id=user_id)
        patch.append(op_set(self._player_path(player_index, "ownedTiles"), []))

        for tile_id in returned_tile_ids:
            tile_index = self._find_tile_index(state=state, tile_id=tile_id)
            if tile_index is None:
                continue
            patch.append(op_set(self._tile_path(tile_index, "ownerUserId"), None))
            patch.append(op_set(self._tile_path(tile_index, "buildingLevel"), 0))

        events.append(
            {
                "type": ServerEventType.PLAYER_STATE_CHANGED.value,
                "playerId": int(user_id),
                "playerState": PlayerState.BANKRUPT.value,
            }
        )

        game_over_event = self._evaluate_game_over(state=state)
        if game_over_event is not None:
            events.append(game_over_event)
            patch.append(op_set("status", state["status"]))
            patch.append(op_set("winnerUserId", state["winnerUserId"]))
        elif self._is_current_turn(turn=turn, user_id=user_id):
            turn_events, turn_patch = self._advance_turn_in_memory(
                state=state, turn=turn
            )
            events.extend(turn_events)
            patch.extend(turn_patch)

        state["revision"] = expected_revision + 1

        await self.repository.save(
            game_id=game_id,
            state=state,
            turn=turn,
            expected_revision=expected_revision,
        )

        return make_patch(
            game_id=game_id,
            revision=state["revision"],
            turn=int(turn["currentPlayerIndex"]),
            events=events,
            patch=patch,
        )

    async def move_to_island(
        self,
        *,
        game_id: int,
        user_id: int,
        island_position: int,
    ) -> dict[str, Any]:
        state, turn = await self.repository.load(game_id=game_id)
        expected_revision = int(state["revision"])

        self._ensure_not_finished(state=state)
        self._ensure_current_turn(turn=turn, user_id=user_id)

        player_index = self._find_player_index(state=state, user_id=user_id)
        player = state["players"][player_index]
        self._ensure_player_state(
            player=player,
            allowed_states={PlayerState.NORMAL.value},
            action="move_to_island",
        )

        from_tile_id = int(player["currentTileId"])
        player["currentTileId"] = int(island_position)
        player["playerState"] = PlayerState.LOCKED.value
        player["stateDuration"] = 3

        events = [
            {
                "type": ServerEventType.PLAYER_MOVED.value,
                "playerId": int(user_id),
                "fromTileId": from_tile_id,
                "toTileId": int(island_position),
                "trigger": MoveTrigger.DICE.value,
                "passGo": False,
            },
            {
                "type": ServerEventType.PLAYER_STATE_CHANGED.value,
                "playerId": int(user_id),
                "playerState": PlayerState.LOCKED.value,
                "stateDuration": 3,
            },
        ]
        patch = [
            op_set(
                self._player_path(player_index, "currentTileId"), int(island_position)
            ),
            op_set(
                self._player_path(player_index, "playerState"),
                PlayerState.LOCKED.value,
            ),
            op_set(self._player_path(player_index, "stateDuration"), 3),
        ]

        state["revision"] = expected_revision + 1

        await self.repository.save(
            game_id=game_id,
            state=state,
            turn=turn,
            expected_revision=expected_revision,
        )

        return make_patch(
            game_id=game_id,
            revision=state["revision"],
            turn=int(turn["currentPlayerIndex"]),
            events=events,
            patch=patch,
        )

    async def handle_island_turn(
        self,
        *,
        game_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        state, turn = await self.repository.load(game_id=game_id)
        expected_revision = int(state["revision"])

        self._ensure_not_finished(state=state)
        self._ensure_current_turn(turn=turn, user_id=user_id)

        player_index = self._find_player_index(state=state, user_id=user_id)
        player = state["players"][player_index]

        if player["playerState"] != PlayerState.LOCKED.value:
            raise ValueError("Player is not locked")

        rolled_dice1, rolled_dice2 = self._roll_dice()
        is_double = rolled_dice1 == rolled_dice2

        events: list[dict[str, Any]] = [
            {
                "type": ServerEventType.DICE_ROLLED.value,
                "playerId": int(user_id),
                "dice": [rolled_dice1, rolled_dice2],
                "isDouble": is_double,
            }
        ]
        patch: list[dict[str, Any]] = []

        if is_double:
            player["playerState"] = PlayerState.NORMAL.value
            player["stateDuration"] = 0

            events.append(
                {
                    "type": ServerEventType.PLAYER_STATE_CHANGED.value,
                    "playerId": int(user_id),
                    "playerState": PlayerState.NORMAL.value,
                    "releasedBy": "DOUBLE",
                }
            )
            patch.append(
                op_set(
                    self._player_path(player_index, "playerState"),
                    PlayerState.NORMAL.value,
                )
            )
            patch.append(op_set(self._player_path(player_index, "stateDuration"), 0))
        else:
            remaining = max(int(player["stateDuration"]) - 1, 0)
            player["stateDuration"] = remaining

            if remaining == 0:
                player["playerState"] = PlayerState.NORMAL.value
                events.append(
                    {
                        "type": ServerEventType.PLAYER_STATE_CHANGED.value,
                        "playerId": int(user_id),
                        "playerState": PlayerState.NORMAL.value,
                        "releasedBy": "TURN_EXPIRED",
                    }
                )
                patch.append(
                    op_set(self._player_path(player_index, "stateDuration"), 0)
                )
                patch.append(
                    op_set(
                        self._player_path(player_index, "playerState"),
                        PlayerState.NORMAL.value,
                    )
                )
            else:
                events.append(
                    {
                        "type": ServerEventType.PLAYER_STATE_CHANGED.value,
                        "playerId": int(user_id),
                        "playerState": PlayerState.LOCKED.value,
                        "stateDuration": remaining,
                    }
                )
                patch.append(
                    op_set(self._player_path(player_index, "stateDuration"), remaining)
                )

        state["revision"] = expected_revision + 1

        await self.repository.save(
            game_id=game_id,
            state=state,
            turn=turn,
            expected_revision=expected_revision,
        )

        return make_patch(
            game_id=game_id,
            revision=state["revision"],
            turn=int(turn["currentPlayerIndex"]),
            events=events,
            patch=patch,
        )

    async def apply_travel(
        self,
        *,
        game_id: int,
        user_id: int,
        destination: int,
    ) -> dict[str, Any]:
        state, turn = await self.repository.load(game_id=game_id)
        expected_revision = int(state["revision"])

        self._ensure_not_finished(state=state)
        self._ensure_current_turn(turn=turn, user_id=user_id)

        player_index = self._find_player_index(state=state, user_id=user_id)
        player = state["players"][player_index]
        self._ensure_player_state(
            player=player,
            allowed_states={PlayerState.NORMAL.value},
            action="apply_travel",
        )

        self._validate_travel_destination(
            state=state,
            current_tile_id=int(player["currentTileId"]),
            destination=int(destination),
        )

        from_tile_id = int(player["currentTileId"])
        player["currentTileId"] = int(destination)

        events = [
            {
                "type": ServerEventType.PLAYER_MOVED.value,
                "playerId": int(user_id),
                "fromTileId": from_tile_id,
                "toTileId": int(destination),
                "trigger": MoveTrigger.TRAVEL.value,
                "passGo": False,
            }
        ]
        patch = [
            op_set(self._player_path(player_index, "currentTileId"), int(destination))
        ]

        state["revision"] = expected_revision + 1

        await self.repository.save(
            game_id=game_id,
            state=state,
            turn=turn,
            expected_revision=expected_revision,
        )

        return make_patch(
            game_id=game_id,
            revision=state["revision"],
            turn=int(turn["currentPlayerIndex"]),
            events=events,
            patch=patch,
        )

    async def advance_turn(
        self,
        *,
        game_id: int,
    ) -> dict[str, Any]:
        state, turn = await self.repository.load(game_id=game_id)
        expected_revision = int(state["revision"])

        self._ensure_not_finished(state=state)

        if self._alive_player_count(state=state) == 0:
            state["status"] = "FINISHED"
            state["winnerUserId"] = None
            state["revision"] = expected_revision + 1

            patch = [
                op_set("status", state["status"]),
                op_set("winnerUserId", state["winnerUserId"]),
            ]

            await self.repository.save(
                game_id=game_id,
                state=state,
                turn=turn,
                expected_revision=expected_revision,
            )

            return make_patch(
                game_id=game_id,
                revision=state["revision"],
                turn=int(turn["currentPlayerIndex"]),
                events=[
                    {
                        "type": ServerEventType.GAME_OVER.value,
                        "reason": GameOverReason.LAST_SURVIVOR.value,
                        "winnerUserId": None,
                    }
                ],
                patch=patch,
            )

        existing_game_over = self._evaluate_game_over(state=state)
        if existing_game_over is not None:
            state["revision"] = expected_revision + 1

            patch = [
                op_set("status", state["status"]),
                op_set("winnerUserId", state["winnerUserId"]),
            ]

            await self.repository.save(
                game_id=game_id,
                state=state,
                turn=turn,
                expected_revision=expected_revision,
            )

            return make_patch(
                game_id=game_id,
                revision=state["revision"],
                turn=int(turn["currentPlayerIndex"]),
                events=[existing_game_over],
                patch=patch,
            )

        events, patch = self._advance_turn_in_memory(state=state, turn=turn)
        state["revision"] = expected_revision + 1

        await self.repository.save(
            game_id=game_id,
            state=state,
            turn=turn,
            expected_revision=expected_revision,
        )

        return make_patch(
            game_id=game_id,
            revision=state["revision"],
            turn=int(turn["currentPlayerIndex"]),
            events=events,
            patch=patch,
        )

    async def get_state(self, *, game_id: int) -> dict[str, Any]:
        return await self.repository.get_state(game_id=game_id)

    async def get_turn(self, *, game_id: int) -> dict[str, Any]:
        return await self.repository.get_turn(game_id=game_id)

    def _roll_dice(self) -> tuple[int, int]:
        return self._dice_roller()

    def _prepare_tiles(self, tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []

        for index, tile in enumerate(tiles):
            current = dict(tile)
            current["tileId"] = int(current.get("tileId", index))

            owner_user_id = current.get("ownerUserId")
            current["ownerUserId"] = (
                int(owner_user_id) if owner_user_id is not None else None
            )
            current["buildingLevel"] = int(
                current.get("buildingLevel", current.get("buildingCount", 0))
            )
            current.pop("buildingCount", None)
            prepared.append(current)

        return prepared

    def _player_path(self, player_index: int, field: str) -> str:
        return f"players.{player_index}.{field}"

    def _tile_path(self, tile_index: int, field: str) -> str:
        return f"tiles.{tile_index}.{field}"

    def _find_player_index(self, *, state: dict[str, Any], user_id: int) -> int:
        for idx, player in enumerate(state["players"]):
            if int(player["userId"]) == int(user_id):
                return idx
        raise ValueError("Player not found")

    def _find_tile_index(
        self,
        *,
        state: dict[str, Any],
        tile_id: int,
    ) -> int | None:
        for idx, tile in enumerate(state.get("tiles", [])):
            if int(tile.get("tileId", -1)) == int(tile_id):
                return idx
        return None

    def _find_tile_by_id(
        self,
        *,
        state: dict[str, Any],
        tile_id: int,
    ) -> dict[str, Any] | None:
        for tile in state.get("tiles", []):
            if int(tile.get("tileId", -1)) == int(tile_id):
                return tile
        return None

    def _release_player_tiles(
        self, *, state: dict[str, Any], user_id: int
    ) -> list[int]:
        released_tile_ids: list[int] = []

        for tile in state.get("tiles", []):
            owner_user_id = tile.get("ownerUserId")
            if owner_user_id is None:
                continue
            if int(owner_user_id) != int(user_id):
                continue

            released_tile_ids.append(int(tile["tileId"]))
            tile["ownerUserId"] = None
            tile["buildingLevel"] = 0

        player_index = self._find_player_index(state=state, user_id=user_id)
        state["players"][player_index]["ownedTiles"] = []

        return released_tile_ids

    def _alive_players(self, *, state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            player
            for player in state["players"]
            if player["playerState"] != PlayerState.BANKRUPT.value
            and bool(player["isAlive"])
        ]

    def _alive_player_count(self, *, state: dict[str, Any]) -> int:
        return len(self._alive_players(state=state))

    def _evaluate_game_over(self, *, state: dict[str, Any]) -> dict[str, Any] | None:
        alive_players = self._alive_players(state=state)

        for condition in self._victory_conditions:
            event = condition.evaluate(state=state, alive_players=alive_players)
            if event is not None:
                return event

        return None

    def _advance_turn_in_memory(
        self,
        *,
        state: dict[str, Any],
        turn: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self._alive_player_count(state=state) == 0:
            state["status"] = "FINISHED"
            state["winnerUserId"] = None
            return (
                [
                    {
                        "type": ServerEventType.GAME_OVER.value,
                        "reason": GameOverReason.LAST_SURVIVOR.value,
                        "winnerUserId": None,
                    }
                ],
                [
                    op_set("status", state["status"]),
                    op_set("winnerUserId", state["winnerUserId"]),
                ],
            )

        current_index = int(turn["currentPlayerIndex"])
        next_index = self._find_next_alive_player_index(
            state=state,
            current_index=current_index,
        )

        if next_index is None:
            state["status"] = "FINISHED"
            state["winnerUserId"] = None
            return (
                [
                    {
                        "type": ServerEventType.GAME_OVER.value,
                        "reason": GameOverReason.LAST_SURVIVOR.value,
                        "winnerUserId": None,
                    }
                ],
                [
                    op_set("status", state["status"]),
                    op_set("winnerUserId", state["winnerUserId"]),
                ],
            )

        wrapped = next_index <= current_index
        if wrapped:
            state["currentRound"] += 1

        game_over_event = self._evaluate_game_over(state=state)
        if game_over_event is not None:
            return (
                [game_over_event],
                [
                    op_set("currentRound", state["currentRound"]),
                    op_set("status", state["status"]),
                    op_set("winnerUserId", state["winnerUserId"]),
                ],
            )

        turn["currentPlayerIndex"] = next_index
        turn["currentUserId"] = int(state["players"][next_index]["userId"])

        return (
            [
                {
                    "type": ServerEventType.TURN_ENDED.value,
                    "nextPlayerId": int(turn["currentUserId"]),
                    "round": int(state["currentRound"]),
                }
            ],
            [op_set("currentRound", int(state["currentRound"]))],
        )

    def _find_next_alive_player_index(
        self,
        *,
        state: dict[str, Any],
        current_index: int,
    ) -> int | None:
        players = state["players"]
        total = len(players)

        for step in range(1, total + 1):
            idx = (current_index + step) % total
            player = players[idx]
            if player["playerState"] != PlayerState.BANKRUPT.value and bool(
                player["isAlive"]
            ):
                return idx

        return None

    def _is_current_turn(self, *, turn: dict[str, Any], user_id: int) -> bool:
        current_user_id = turn.get("currentUserId")
        if current_user_id is None:
            return False
        return int(current_user_id) == int(user_id)

    def _ensure_current_turn(self, *, turn: dict[str, Any], user_id: int) -> None:
        if not self._is_current_turn(turn=turn, user_id=user_id):
            raise ValueError("Not your turn")

    def _ensure_not_finished(self, *, state: dict[str, Any]) -> None:
        if state.get("status") == "FINISHED":
            raise ValueError("Game already finished")

    def _ensure_player_state(
        self,
        *,
        player: dict[str, Any],
        allowed_states: set[str],
        action: str,
    ) -> None:
        current_state = str(player.get("playerState"))
        if current_state not in allowed_states:
            raise ValueError(f"Invalid player state for {action}")
        if current_state == PlayerState.BANKRUPT.value or not bool(
            player.get("isAlive", True)
        ):
            raise ValueError("Player is bankrupt")

    def _travel_destinations(self, *, state: dict[str, Any]) -> set[int]:
        if self._immutable_travel_destinations:
            return set(self._immutable_travel_destinations)

        explicit_destinations = state.get("travelDestinations")
        if isinstance(explicit_destinations, list) and explicit_destinations:
            return {int(destination) for destination in explicit_destinations}

        allowed: set[int] = set()
        fallback: set[int] = set()

        for index, tile in enumerate(state.get("tiles", [])):
            tile_id = int(tile.get("tileId", index))
            fallback.add(tile_id)

            if tile.get("isTravelTarget") is True:
                allowed.add(tile_id)
                continue

            if tile.get("travelEnabled") is True:
                allowed.add(tile_id)
                continue

            incoming_triggers = tile.get("allowedIncomingTriggers")
            if isinstance(incoming_triggers, list):
                trigger_values = {str(trigger) for trigger in incoming_triggers}
                if MoveTrigger.TRAVEL.value in trigger_values:
                    allowed.add(tile_id)

        if allowed:
            return allowed
        return fallback

    def _validate_travel_destination(
        self,
        *,
        state: dict[str, Any],
        current_tile_id: int,
        destination: int,
    ) -> None:
        current_tile = self._find_tile_by_id(state=state, tile_id=current_tile_id)
        if current_tile is None:
            raise ValueError("Current tile not found")

        current_tile_type = str(current_tile.get("type", ""))
        if current_tile_type != TileType.TRAVEL.value:
            raise ValueError("Travel can only be used on TRAVEL tile")

        allowed_destinations = self._travel_destinations(state=state)
        if not allowed_destinations:
            raise ValueError("Travel destinations not configured")

        if int(destination) not in allowed_destinations:
            raise ValueError("Invalid travel destination")

        if int(destination) == int(current_tile_id):
            raise ValueError("Invalid travel destination")
