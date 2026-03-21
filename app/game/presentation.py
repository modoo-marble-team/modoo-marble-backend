from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any

from app.game.board import BOARD
from app.game.enums import PlayerState
from app.game.models import GameState, PlayerGameState
from app.game.rules import PHASE_GAME_OVER, serialize_prompt
from app.game.timer import TURN_TIMEOUT_SECONDS

PLAYER_COLORS = ["#EF5350", "#42A5F5", "#66BB6A", "#FFD15B"]
PLAYER_STATE_MAP = {
    PlayerState.NORMAL.value: "normal",
    PlayerState.LOCKED.value: "locked",
    PlayerState.BANKRUPT.value: "bankrupt",
}

NETWORK_KEY_MAP = {
    "player_id": "playerId",
    "next_player_id": "nextPlayerId",
    "from_tile_id": "fromTileId",
    "to_tile_id": "toTileId",
    "tile_id": "tileId",
    "owner_id": "ownerId",
    "current_tile_id": "currentTileId",
    "current_player_id": "currentPlayerId",
    "owned_tile_ids": "ownedTiles",
    "owned_tiles": "ownedTiles",
    "building_level": "buildingLevel",
    "building_levels": "buildingLevels",
    "state_duration": "stateDuration",
    "known_revision": "knownRevision",
    "current_revision": "currentRevision",
    "winner_player_id": "winnerPlayerId",
    "winner_id": "winnerId",
    "player_state": "playerState",
    "turn_order": "turnOrder",
    "room_id": "roomId",
    "game_id": "gameId",
    "prompt_id": "promptId",
    "timeout_sec": "timeoutSec",
    "default_choice": "defaultChoice",
}

PATCH_PATH_MAP = {
    "current_tile_id": "currentTileId",
    "current_player_id": "currentPlayerId",
    "owned_tile_ids": "ownedTiles",
    "owned_tiles": "ownedTiles",
    "owner_id": "ownerId",
    "building_level": "buildingLevel",
    "building_levels": "buildingLevels",
    "state_duration": "stateDuration",
    "winner_id": "winnerId",
    "player_state": "playerState",
    "turn_order": "turnOrder",
    "pending_prompt": "prompt",
}


def _normalize_scalar(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _normalize_key(key: str) -> str:
    return NETWORK_KEY_MAP.get(key, key)


def _normalize_payload(value: Any) -> Any:
    if is_dataclass(value) and hasattr(value, "to_json"):
        return _normalize_payload(value.to_json())

    if isinstance(value, dict):
        return {
            _normalize_key(str(key)): _normalize_payload(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]

    return _normalize_scalar(value)


def _normalize_patch_path(path: str) -> str:
    return ".".join(PATCH_PATH_MAP.get(part, part) for part in path.split("."))


def _serialize_patch_ops(patch_ops: list[dict] | None) -> list[dict]:
    serialized: list[dict] = []

    for item in patch_ops or []:
        payload = {
            "op": _normalize_scalar(item.get("op")),
            "path": _normalize_patch_path(str(item.get("path", ""))),
        }
        if "value" in item:
            payload["value"] = _normalize_payload(item.get("value"))
        serialized.append(payload)

    return serialized


def _ordered_players(state: GameState) -> list[PlayerGameState]:
    return state.ordered_players()


def serialize_game_snapshot(state: GameState) -> dict[str, Any]:
    tiles = []
    for tile in BOARD:
        tile_state = state.tile(tile.tile_id)
        owner_id = tile_state.owner_id if tile_state else None
        building_level = tile_state.building_level if tile_state else 0
        tiles.append(
            {
                "id": tile.tile_id,
                "name": tile.name,
                "type": str(tile.tile_type),
                "ownerId": str(owner_id) if owner_id is not None else None,
                "buildingLevel": building_level,
                "price": tile.price,
            }
        )

    players = []
    for index, player in enumerate(_ordered_players(state)):
        players.append(
            {
                "playerId": str(player.player_id),
                "nickname": player.nickname,
                "currentTileId": player.current_tile_id,
                "balance": player.balance,
                "ownedTiles": player.owned_tiles,
                "isInJail": player.player_state == PlayerState.LOCKED,
                "stateDuration": player.state_duration,
                "isBankrupt": player.player_state == PlayerState.BANKRUPT,
                "playerState": PLAYER_STATE_MAP.get(
                    player.player_state.value,
                    "normal",
                ),
                "color": PLAYER_COLORS[index % len(PLAYER_COLORS)],
            }
        )

    return {
        "roomId": state.room_id,
        "gameId": state.game_id,
        "rulesetVersion": state.ruleset_version,
        "revision": state.revision,
        "phase": state.phase if state.status == "playing" else PHASE_GAME_OVER,
        "players": players,
        "tiles": tiles,
        "currentPlayerId": str(state.current_player_id),
        "round": state.round,
        "turnTimeoutSec": TURN_TIMEOUT_SECONDS,
        "prompt": serialize_prompt(state.pending_prompt),
        "isGameOver": state.status != "playing",
        "winnerId": str(state.winner_id) if state.winner_id is not None else None,
    }


def serialize_game_patch(
    state: GameState,
    *,
    events: list[dict],
    patches: list[dict] | None = None,
    include_snapshot: bool = True,
) -> dict[str, Any]:
    return {
        "gameId": state.game_id,
        "revision": state.revision,
        "turn": state.turn,
        "events": [_normalize_payload(event) for event in events],
        "patch": _serialize_patch_ops(patches),
        "snapshot": serialize_game_snapshot(state) if include_snapshot else None,
    }
