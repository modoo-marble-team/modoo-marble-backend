from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class PlayerGameState(TypedDict):
    """Redis에 저장되는 플레이어 1명의 상태"""

    playerId: int
    user_id: int
    nickname: str
    balance: int  # 잔액 (단위: 1). 초기 5000 → 표시 50억
    currentTileId: int  # 현재 위치 타일 번호. 초기 0
    playerState: str  # "NORMAL" 또는 "LOCKED"
    stateDuration: int  # 무인도 남은 턴 수
    consecutiveDoubles: int  # 연속 더블 횟수
    ownedTiles: list[int]  # 소유한 타일 번호 목록
    buildingLevels: dict[str, int]  # {"tile_id": 건물레벨}
    turnOrder: int  # 턴 순서 (0부터)
    balance: int
    current_tile_id: int
    state: str
    state_duration: int
    consecutive_doubles: int
    owned_tile_ids: list[int]
    building_levels: dict[str, int]
    turn_order: int


class TileGameState(TypedDict):
    """Redis에 저장되는 타일 1개의 상태 (PROPERTY만 사용)"""

    ownerId: int | None
    buildingLevel: int  # 0~7
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

