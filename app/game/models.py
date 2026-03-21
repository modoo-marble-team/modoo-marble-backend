from __future__ import annotations

import types
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from app.game.enums import PlayerState
from app.game.game_rules import GAME_RULESET_VERSION

T = TypeVar("T", bound="JsonModel")


def _is_none_type(annotation: Any) -> bool:
    return annotation is type(None)


def _is_union_type(annotation: Any) -> bool:
    return get_origin(annotation) in (types.UnionType, getattr(types, "UnionType"))


def _unpack_dict_key(annotation: Any, raw_key: Any) -> Any:
    if annotation is int:
        return int(raw_key)
    if annotation is str:
        return str(raw_key)
    return raw_key


def _unpack_value(annotation: Any, value: Any) -> Any:
    if value is None:
        return None

    origin = get_origin(annotation)
    if annotation is Any or annotation is None:
        return value

    if _is_union_type(annotation):
        for candidate in get_args(annotation):
            if _is_none_type(candidate) and value is None:
                return None
            if _is_none_type(candidate):
                continue
            try:
                return _unpack_value(candidate, value)
            except (TypeError, ValueError, KeyError):
                continue
        return value

    if origin is list:
        (item_type,) = get_args(annotation) or (Any,)
        return [_unpack_value(item_type, item) for item in value]

    if origin is dict:
        key_type, value_type = get_args(annotation) or (Any, Any)
        unpacked: dict[Any, Any] = {}
        for raw_key, raw_value in value.items():
            unpacked[_unpack_dict_key(key_type, raw_key)] = _unpack_value(
                value_type,
                raw_value,
            )
        return unpacked

    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return annotation(value)
        if is_dataclass(annotation):
            return annotation.from_json(value)

    return value


def _pack_value(value: Any) -> Any:
    if is_dataclass(value):
        packed: dict[str, Any] = {}
        for field_def in fields(value):
            packed[field_def.name] = _pack_value(getattr(value, field_def.name))
        return packed

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, list):
        return [_pack_value(item) for item in value]

    if isinstance(value, dict):
        return {str(key): _pack_value(item) for key, item in value.items()}

    return value


@dataclass(slots=True)
class JsonModel:
    @classmethod
    def from_json(cls: type[T], data: Mapping[str, Any]) -> T:
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}

        for field_def in fields(cls):
            if field_def.name not in data:
                has_default = field_def.default is not MISSING
                has_factory = field_def.default_factory is not MISSING
                if has_default or has_factory:
                    continue
                raise KeyError(f"Missing required field: {field_def.name}")

            kwargs[field_def.name] = _unpack_value(
                hints.get(field_def.name, Any),
                data[field_def.name],
            )

        return cls(**kwargs)

    def to_json(self) -> dict[str, Any]:
        return _pack_value(self)


@dataclass(slots=True)
class PromptChoice(JsonModel):
    id: str
    label: str
    value: str
    description: str | None = None


@dataclass(slots=True)
class PendingPrompt(JsonModel):
    prompt_id: str
    type: str
    player_id: int
    title: str
    message: str
    timeout_sec: int
    choices: list[PromptChoice]
    payload: dict[str, Any]
    default_choice: str


@dataclass(slots=True)
class PlayerGameState(JsonModel):
    player_id: int
    nickname: str
    balance: int
    current_tile_id: int
    player_state: PlayerState
    state_duration: int
    consecutive_doubles: int
    owned_tiles: list[int] = field(default_factory=list)
    building_levels: dict[int, int] = field(default_factory=dict)
    turn_order: int = 0

    @property
    def is_bankrupt(self) -> bool:
        return self.player_state == PlayerState.BANKRUPT

    @property
    def is_locked(self) -> bool:
        return self.player_state == PlayerState.LOCKED


@dataclass(slots=True)
class TileGameState(JsonModel):
    owner_id: int | None
    building_level: int


@dataclass(slots=True)
class GameState(JsonModel):
    game_id: str
    room_id: str
    revision: int
    turn: int
    round: int
    current_player_id: int
    status: str
    phase: str
    players: dict[int, PlayerGameState]
    tiles: dict[int, TileGameState]
    pending_prompt: PendingPrompt | None
    ruleset_version: str = GAME_RULESET_VERSION
    winner_id: int | None = None

    def player(self, player_id: int) -> PlayerGameState | None:
        return self.players.get(player_id)

    def require_player(self, player_id: int) -> PlayerGameState:
        player = self.player(player_id)
        if player is None:
            raise KeyError(f"Missing player: {player_id}")
        return player

    def tile(self, tile_id: int) -> TileGameState | None:
        return self.tiles.get(tile_id)

    def ordered_players(self) -> list[PlayerGameState]:
        return sorted(self.players.values(), key=lambda player: player.turn_order)

    def active_players(self) -> list[PlayerGameState]:
        return [player for player in self.ordered_players() if not player.is_bankrupt]

    def clone(self) -> GameState:
        return GameState.from_json(self.to_json())
