from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import socketio

from services.game.runtime import game_manager


sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

_sid_to_session: dict[str, Optional[str]] = {}
_session_to_sids: dict[str, set[str]] = defaultdict(set)


async def _bind_session(sid: str, session_id: str) -> None:
    old_session = _sid_to_session.get(sid)
    if old_session:
        _session_to_sids[old_session].discard(sid)
        await sio.leave_room(sid, old_session)
    _sid_to_session[sid] = session_id
    _session_to_sids[session_id].add(sid)
    await sio.enter_room(sid, session_id)


async def _unbind_session(sid: str) -> Optional[str]:
    session_id = _sid_to_session.pop(sid, None)
    if session_id:
        _session_to_sids[session_id].discard(sid)
        await sio.leave_room(sid, session_id)
    return session_id


def _bound_session(sid: str) -> Optional[str]:
    return _sid_to_session.get(sid)


async def _emit_error(sid: str, event: str, message: str) -> None:
    await sio.emit(
        "game_error",
        {
            "event": event,
            "message": message,
        },
        to=sid,
    )


def _session_or_none(session_id: str):
    try:
        return game_manager.get_session(session_id)
    except KeyError:
        return None


@sio.event
async def connect(sid, environ, auth):
    _sid_to_session[sid] = None
    await sio.emit("connected", {"sid": sid}, to=sid)


@sio.event
async def disconnect(sid):
    await _unbind_session(sid)


@sio.event
async def create_session(sid, data):
    payload = data or {}
    player_ids = payload.get("player_ids") or ["P1", "P2", "P3", "P4"]
    session_id = payload.get("session_id")
    try:
        session = game_manager.create_session(
            player_ids=list(player_ids),
            session_id=session_id,
        )
    except (TypeError, ValueError) as exc:
        await _emit_error(sid, "create_session", str(exc))
        return

    await _bind_session(sid, session.session_id)
    await sio.emit(
        "session_created",
        {
            "session_id": session.session_id,
            "state": session.snapshot(),
        },
        to=sid,
    )


@sio.event
async def join_session(sid, data):
    payload = data or {}
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        await _emit_error(sid, "join_session", "session_id is required.")
        return

    session = _session_or_none(session_id)
    if session is None:
        await _emit_error(sid, "join_session", f"Session '{session_id}' not found.")
        return

    await _bind_session(sid, session_id)
    await sio.emit(
        "session_joined",
        {
            "session_id": session_id,
            "state": session.snapshot(),
        },
        to=sid,
    )


@sio.event
async def close_session(sid, data):
    payload = data or {}
    session_id = _bound_session(sid) or payload.get("session_id")
    if not session_id:
        await _emit_error(sid, "close_session", "No bound session.")
        return

    game_manager.close_session(session_id)
    await sio.emit(
        "session_closed",
        {
            "session_id": session_id,
        },
        room=session_id,
    )

    for member_sid in list(_session_to_sids.get(session_id, set())):
        await _unbind_session(member_sid)


async def _with_session(sid: str, event_name: str):
    session_id = _bound_session(sid)
    if not session_id:
        await _emit_error(
            sid, event_name, "No session bound. create_session/join_session first."
        )
        return None, None

    session = _session_or_none(session_id)
    if session is None:
        await _emit_error(sid, event_name, f"Session '{session_id}' not found.")
        return None, None

    return session_id, session


@sio.event
async def get_state(sid, data):
    session_id, session = await _with_session(sid, "get_state")
    if not session:
        return
    await sio.emit(
        "state",
        {
            "session_id": session_id,
            "state": session.snapshot(),
        },
        to=sid,
    )


@sio.event
async def get_result(sid, data):
    session_id, session = await _with_session(sid, "get_result")
    if not session:
        return
    await sio.emit(
        "result",
        {
            "session_id": session_id,
            "result": session.get_result(),
        },
        to=sid,
    )


async def _run_update_event(
    sid: str,
    event_name: str,
    callback,
) -> None:
    session_id, session = await _with_session(sid, event_name)
    if not session:
        return

    try:
        event = callback(session)
    except ValueError as exc:
        await _emit_error(sid, event_name, str(exc))
        return

    await sio.emit(
        "update",
        {
            "session_id": session_id,
            "event_name": event_name,
            "event": event,
            "state": session.snapshot(),
        },
        room=session_id,
    )


@sio.event
async def roll(sid, data):
    payload = data or {}
    forced_value = payload.get("forced_value")
    await _run_update_event(
        sid,
        "roll",
        lambda session: session.roll_current_player(forced_value),
    )


@sio.event
async def buy(sid, data):
    await _run_update_event(sid, "buy", lambda session: session.buy_current_tile())


@sio.event
async def build(sid, data):
    await _run_update_event(sid, "build", lambda session: session.build_current_tile())


@sio.event
async def sell_next(sid, data):
    await _run_update_event(sid, "sell_next", lambda session: session.sell_next_asset())


@sio.event
async def end_turn(sid, data):
    await _run_update_event(sid, "end_turn", lambda session: session.end_turn())
