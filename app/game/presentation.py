from __future__ import annotations

from typing import Any

from app.game.board import BOARD
from app.game.enums import PlayerState
from app.game.rules import PHASE_GAME_OVER, serialize_prompt
from app.game.schemas import GameState, PlayerGameState
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
    "building_level": "buildingLevel",
    "state_duration": "stateDuration",
    "known_revision": "knownRevision",
    "current_revision": "currentRevision",
    "winner_player_id": "winnerPlayerId",
    "state": "playerState",
}

PATCH_PATH_MAP = {
    "current_tile_id": "currentTileId",
    "current_player_id": "currentPlayerId",
    "owned_tile_ids": "ownedTiles",
    "owner_id": "ownerId",
    "building_level": "buildingLevel",
    "state_duration": "stateDuration",
}


def _ordered_players(state: GameState) -> list[PlayerGameState]:
    return sorted(state["players"].values(), key=lambda player: player["turnOrder"])


def _normalize_scalar(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _normalize_key(key: str) -> str:
    return NETWORK_KEY_MAP.get(key, key)


def _normalize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            out_key = _normalize_key(str(key))
            normalized[out_key] = _normalize_payload(item)
        return normalized

    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]

    return _normalize_scalar(value)


def _normalize_patch_path(path: str) -> str:
    return ".".join(PATCH_PATH_MAP.get(part, part) for part in path.split("."))


def _serialize_patch_ops(patch: list[dict] | None) -> list[dict]:
    serialized: list[dict] = []

    for item in patch or []:
        payload = {
            "op": _normalize_scalar(item.get("op")),
            "path": _normalize_patch_path(str(item.get("path", ""))),
        }
        if "value" in item:
            payload["value"] = _normalize_payload(item.get("value"))
        serialized.append(payload)

    return serialized


def serialize_game_snapshot(state: GameState) -> dict:
    players = _ordered_players(state)
    tiles = []

    for tile in BOARD:
        tile_state = state["tiles"].get(
            str(tile.tile_id),
            {"ownerId": None, "buildingLevel": 0},
        )
        owner_id = tile_state.get("ownerId")
        tiles.append(
            {
                "id": tile.tile_id,
                "name": tile.name,
                "type": str(tile.tile_type),
                "ownerId": str(owner_id) if owner_id is not None else None,
                "buildingLevel": tile_state.get("buildingLevel", 0),
                "price": tile.price,
            }
        )

    serialized_players = []
    for index, player in enumerate(players):
        serialized_players.append(
            {
                "playerId": str(player["playerId"]),
                "name": player["nickname"],
                "nickname": player["nickname"],
                "currentTileId": player["currentTileId"],
                "balance": player["balance"],
                "ownedTiles": player["ownedTiles"],
                "isInJail": player["playerState"] == PlayerState.LOCKED,
                "stateDuration": player["stateDuration"],
                "isBankrupt": player["playerState"] == PlayerState.BANKRUPT,
                "playerState": PLAYER_STATE_MAP.get(player["playerState"], "normal"),
                "color": PLAYER_COLORS[index % len(PLAYER_COLORS)],
            }
        )

    current_player_id = str(state["current_player_id"])
    return {
        "roomId": state["room_id"],
        "gameId": state["game_id"],
        "revision": state["revision"],
        "phase": state["phase"] if state["status"] == "playing" else PHASE_GAME_OVER,
        "players": serialized_players,
        "tiles": tiles,
        "currentPlayerId": current_player_id,
        "round": state["round"],
        "turnTimeoutSec": TURN_TIMEOUT_SECONDS,
        "prompt": serialize_prompt(state.get("pending_prompt")),
        "isGameOver": state["status"] != "playing",
        "winnerId": None,
    }


def serialize_game_patch(
    state: GameState,
    *,
    events: list[dict],
    patch: list[dict] | None = None,
    include_snapshot: bool = True,
) -> dict:
    return {
        "gameId": state["game_id"],
        "revision": state["revision"],
        "turn": state["turn"],
        "events": [_normalize_payload(event) for event in events],
        "patch": _serialize_patch_ops(patch),
        "snapshot": serialize_game_snapshot(state) if include_snapshot else None,
    }
