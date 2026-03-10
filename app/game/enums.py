from __future__ import annotations

from enum import Enum


class TileType(str, Enum):
    """보드 타일의 종류"""

    START = "START"
    PROPERTY = "PROPERTY"
    EVENT = "EVENT"
    CHANCE = "CHANCE"
    MOVE_TO_ISLAND = "MOVE_TO_ISLAND"
    ISLAND = "ISLAND"


class PlayerState(str, Enum):
    """플레이어의 현재 상태"""

    NORMAL = "NORMAL"
    LOCKED = "LOCKED"  # 무인도에 갇힌 상태


class ActionType(str, Enum):
    """클라이언트 → 서버 game:action의 type"""

    ROLL_DICE = "ROLL_DICE"
    BUY_PROPERTY = "BUY_PROPERTY"
    SELL_PROPERTY = "SELL_PROPERTY"
    END_TURN = "END_TURN"


class ServerEventType(str, Enum):
    """서버 → 클라이언트 game:patch의 events 안에 들어가는 타입"""

    DICE_ROLLED = "DICE_ROLLED"
    PLAYER_MOVED = "PLAYER_MOVED"
    LANDED = "LANDED"
    PAID_TOLL = "PAID_TOLL"
    BOUGHT_PROPERTY = "BOUGHT_PROPERTY"
    SOLD_PROPERTY = "SOLD_PROPERTY"
    TURN_ENDED = "TURN_ENDED"
    PLAYER_STATE_CHANGED = "PLAYER_STATE_CHANGED"
    CHANCE_RESOLVED = "CHANCE_RESOLVED"
    SYNCED = "SYNCED"
    GAME_OVER = "GAME_OVER"


class PatchOp(str, Enum):
    """game:patch 안의 patch 배열에서 쓰는 연산"""

    SET = "set"  # 값을 덮어씀
    INC = "inc"  # 숫자를 더하거나 뺌
    PUSH = "push"  # 배열에 항목 추가
    REMOVE = "remove"  # 배열에서 항목 제거 또는 키 삭제
