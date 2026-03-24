from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import main


@pytest.mark.asyncio
async def test_disconnect_keeps_user_online_when_another_socket_exists(monkeypatch):
    original_sid_to_user = dict(main._sid_to_user)
    original_disconnect_tasks = dict(main._room_disconnect_tasks)

    main._sid_to_user.clear()
    main._sid_to_user.update({"sid-1": 1, "sid-2": 1})
    main._room_disconnect_tasks.clear()

    handle_game_socket_disconnect = AsyncMock()
    set_offline_and_emit = AsyncMock()
    schedule_cleanup = Mock()

    monkeypatch.setattr(
        main, "handle_game_socket_disconnect", handle_game_socket_disconnect
    )
    monkeypatch.setattr(main, "set_offline_and_emit", set_offline_and_emit)
    monkeypatch.setattr(main, "_schedule_room_disconnect_cleanup", schedule_cleanup)
    monkeypatch.setattr(
        main.User,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(nickname="guest")),
    )

    try:
        await main.disconnect("sid-1")

        handle_game_socket_disconnect.assert_awaited_once_with(sid="sid-1", user_id=1)
        schedule_cleanup.assert_not_called()
        set_offline_and_emit.assert_not_awaited()
        assert main._sid_to_user == {"sid-2": 1}
    finally:
        main._sid_to_user.clear()
        main._sid_to_user.update(original_sid_to_user)
        main._room_disconnect_tasks.clear()
        main._room_disconnect_tasks.update(original_disconnect_tasks)


@pytest.mark.asyncio
async def test_disconnect_schedules_cleanup_for_last_socket(monkeypatch):
    original_sid_to_user = dict(main._sid_to_user)
    original_disconnect_tasks = dict(main._room_disconnect_tasks)

    main._sid_to_user.clear()
    main._sid_to_user.update({"sid-1": 1})
    main._room_disconnect_tasks.clear()

    handle_game_socket_disconnect = AsyncMock()
    set_offline_and_emit = AsyncMock()
    schedule_cleanup = Mock()

    monkeypatch.setattr(
        main, "handle_game_socket_disconnect", handle_game_socket_disconnect
    )
    monkeypatch.setattr(main, "set_offline_and_emit", set_offline_and_emit)
    monkeypatch.setattr(main, "_schedule_room_disconnect_cleanup", schedule_cleanup)
    monkeypatch.setattr(
        main.User,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(nickname="guest")),
    )

    try:
        await main.disconnect("sid-1")

        handle_game_socket_disconnect.assert_awaited_once_with(sid="sid-1", user_id=1)
        schedule_cleanup.assert_called_once_with(1)
        set_offline_and_emit.assert_awaited_once_with(
            main.sio,
            user_id="1",
            nickname="guest",
        )
        assert main._sid_to_user == {}
    finally:
        main._sid_to_user.clear()
        main._sid_to_user.update(original_sid_to_user)
        main._room_disconnect_tasks.clear()
        main._room_disconnect_tasks.update(original_disconnect_tasks)
