from __future__ import annotations

from typing import Any, TypedDict


class PlayerGameState(TypedDict):
    """Redis에 저장되는 플레이어 1명의 상태"""

    playerId: int
    nickname: str
    balance: int  # 잔액 (단위: 1). 초기 5000 → 표시 50억
    currentTileId: int  # 현재 위치 타일 번호. 초기 0
    playerState: str  # "NORMAL" 또는 "LOCKED"
    stateDuration: int  # 무인도 남은 턴 수
    consecutiveDoubles: int  # 연속 더블 횟수
    ownedTiles: list[int]  # 소유한 타일 번호 목록
    buildingLevels: dict[str, int]  # {"tile_id": 건물레벨}
    turnOrder: int  # 턴 순서 (0부터)


class TileGameState(TypedDict):
    """Redis에 저장되는 타일 1개의 상태 (PROPERTY만 사용)"""

    ownerId: int | None
    buildingLevel: int  # 0~7


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


class ServerPatchOperation(TypedDict):
    """
    ⚠️ 서버 전용.
    서버가 직접 만들어서 클라이언트에게 보내는 것.
    클라이언트로부터 절대 받으면 안 됨.
    """

    op: str  # "set" | "inc" | "push" | "remove"
    path: str  # 점(.) 구분 경로. 예: "players.1.balance"
    value: Any


class GamePatch(TypedDict):
    """game:patch 소켓 메시지 전체 구조"""

    game_id: str
    revision: int
    turn: int
    events: list[dict[str, Any]]  # 연출/로그용 이벤트 목록
    patch: list[ServerPatchOperation]  # 실제 상태 변경 목록
    snapshot: GameState | None  # 재연결 시에만 전체 상태 담음
