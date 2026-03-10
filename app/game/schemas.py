from __future__ import annotations

from typing import Any, TypedDict


class PlayerGameState(TypedDict):
    """Redis에 저장되는 플레이어 1명의 상태"""

    user_id: int
    nickname: str
    balance: int  # 잔액 (만 단위). 초기 100
    current_tile_id: int  # 현재 위치 타일 번호. 초기 0
    state: str  # "NORMAL" 또는 "LOCKED"
    state_duration: int  # 무인도 남은 턴 수
    consecutive_doubles: int  # 연속 더블 횟수
    owned_tile_ids: list[int]  # 소유한 타일 번호 목록
    building_levels: dict[str, int]  # {"tile_id": 건물레벨}
    is_bankrupt: bool
    turn_order: int  # 턴 순서 (0부터)


class TileGameState(TypedDict):
    """Redis에 저장되는 타일 1개의 상태 (PROPERTY만 사용)"""

    owner_id: int | None
    building_level: int  # 0~7


class GameState(TypedDict):
    """Redis에 저장되는 게임 전체 상태"""

    game_id: str
    room_id: str
    revision: int  # 상태 변경 횟수. patch마다 +1
    turn: int  # 현재 턴 번호 (1부터)
    round: int  # 현재 라운드 (1부터)
    current_player_id: int  # 현재 턴인 플레이어 user_id
    status: str  # "playing" 또는 "finished"
    players: dict[str, PlayerGameState]  # key = str(user_id)
    tiles: dict[str, TileGameState]  # key = str(tile_id)


class PatchOperation(TypedDict):
    """game:patch 안의 patch 배열 원소 1개"""

    op: str  # "set" | "inc" | "push" | "remove"
    path: str  # 점(.) 구분 경로. 예: "players.1.balance"
    value: Any


class GamePatch(TypedDict):
    """game:patch 소켓 메시지 전체 구조"""

    game_id: str
    revision: int
    turn: int
    events: list[dict[str, Any]]  # 연출/로그용 이벤트 목록
    patch: list[PatchOperation]  # 실제 상태 변경 목록
    snapshot: GameState | None  # 재연결 시에만 전체 상태 담음
