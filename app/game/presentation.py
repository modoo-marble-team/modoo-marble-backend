from __future__ import annotations

from app.game.board import BOARD
from app.game.enums import PlayerState
from app.game.rules import PHASE_GAME_OVER, serialize_prompt
from app.game.schemas import GameState
from app.game.timer import TURN_TIMEOUT_SECONDS

PLAYER_COLORS = ["#EF5350", "#42A5F5", "#66BB6A", "#FFD15B"]
PLAYER_STATE_MAP = {
    PlayerState.NORMAL: "normal",
    PlayerState.LOCKED: "locked",
    PlayerState.BANKRUPT: "bankrupt",
}


def _ordered_players(state: GameState) -> list[dict]:
    return sorted(state["players"].values(), key=lambda player: player["turnOrder"])


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
    include_snapshot: bool = True,
) -> dict:
    return {
        "gameId": state["game_id"],
        "revision": state["revision"],
        "turn": state["turn"],  # 숫자 그대로 유지
        "events": events,
        "patch": [],
        "snapshot": serialize_game_snapshot(state) if include_snapshot else None,
    }
