from __future__ import annotations

from typing import Any

from app.game.enums import PatchOp


def op_set(path: str, value: Any) -> dict[str, Any]:
    return {
        "op": PatchOp.SET.value,
        "path": path,
        "value": value,
    }


def op_inc(path: str, value: int) -> dict[str, Any]:
    return {
        "op": PatchOp.INC.value,
        "path": path,
        "value": value,
    }


def op_push(path: str, value: Any) -> dict[str, Any]:
    return {
        "op": PatchOp.PUSH.value,
        "path": path,
        "value": value,
    }


def op_remove(path: str, value: Any) -> dict[str, Any]:
    return {
        "op": PatchOp.REMOVE.value,
        "path": path,
        "value": value,
    }


def make_patch(
    *,
    game_id: int,
    revision: int,
    turn: int,
    events: list[dict[str, Any]],
    patch: list[dict[str, Any]],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "gameId": game_id,
        "revision": revision,
        "turn": turn,
        "events": events,
        "patch": patch,
    }
    if snapshot is not None:
        payload["snapshot"] = snapshot
    return payload
