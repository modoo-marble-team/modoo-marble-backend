from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class PlayerGameState(TypedDict):
    user_id: int
    nickname: str
    balance: int
    current_tile_id: int
    state: str
    state_duration: int
    consecutive_doubles: int
    owned_tile_ids: list[int]
    building_levels: dict[str, int]
    turn_order: int


class TileGameState(TypedDict):
    owner_id: int | None
    building_level: int


class PromptChoice(TypedDict):
    id: str
    label: str
    value: str
    description: NotRequired[str]


class PendingPrompt(TypedDict):
    prompt_id: str
    type: str
    player_id: int
    title: str
    message: str
    timeout_sec: int
    choices: list[PromptChoice]
    payload: dict[str, Any]
    default_choice: str


class GameState(TypedDict):
    game_id: str
    room_id: str
    revision: int
    turn: int
    round: int
    current_player_id: int
    status: str
    phase: str
    players: dict[str, PlayerGameState]
    tiles: dict[str, TileGameState]
    pending_prompt: PendingPrompt | None


class ServerPatchOperation(TypedDict):
    op: str
    path: str
    value: Any


class GamePatch(TypedDict):
    game_id: str
    revision: int
    turn: int
    events: list[dict[str, Any]]
    patch: list[ServerPatchOperation]
    snapshot: GameState | None

