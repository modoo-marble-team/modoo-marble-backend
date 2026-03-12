from __future__ import annotations

from enum import StrEnum


class TileType(StrEnum):
    START = "START"
    PROPERTY = "PROPERTY"
    EVENT = "EVENT"
    CHANCE = "CHANCE"
    MOVE_TO_ISLAND = "MOVE_TO_ISLAND"
    ISLAND = "ISLAND"
    TRAVEL = "TRAVEL"
    AI = "AI"


class PlayerState(StrEnum):
    NORMAL = "NORMAL"
    LOCKED = "LOCKED"
    BANKRUPT = "BANKRUPT"


class ActionType(StrEnum):
    ROLL_DICE = "ROLL_DICE"
    BUY_PROPERTY = "BUY_PROPERTY"
    SELL_PROPERTY = "SELL_PROPERTY"
    END_TURN = "END_TURN"


class ServerEventType(StrEnum):
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


class PatchOp(StrEnum):
    SET = "set"
    INC = "inc"
    PUSH = "push"
    REMOVE = "remove"

