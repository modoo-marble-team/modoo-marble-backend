from __future__ import annotations

import json
from typing import Any

from app.redis_client import get_redis

_PRESENCE_KEY = "modoo:online_users"


async def set_online(*, user_id: str, nickname: str, status: str = "online") -> None:
    redis = get_redis()
    data = json.dumps({"id": user_id, "nickname": nickname, "status": status})
    await redis.hset(_PRESENCE_KEY, user_id, data)


async def update_status(*, user_id: str, status: str) -> None:
    """이미 온라인인 유저의 상태만 업데이트 (lobby / in_room / playing).

    hget → modify → hset 패턴의 race condition을 방지하기 위해
    유저별 Redis lock으로 원자성을 보장한다.
    """
    redis = get_redis()
    async with redis.lock(f"presence:lock:{user_id}", timeout=5):
        raw = await redis.hget(_PRESENCE_KEY, user_id)
        if raw is None:
            return
        data = json.loads(raw)
        data["status"] = status
        await redis.hset(_PRESENCE_KEY, user_id, json.dumps(data))


async def set_offline(*, user_id: str) -> None:
    redis = get_redis()
    await redis.hdel(_PRESENCE_KEY, user_id)


async def emit_user_status_changed(
    sio,
    *,
    user_id: str,
    nickname: str,
    status: str,
) -> None:
    await sio.emit(
        "user_status_changed",
        {
            "id": user_id,
            "nickname": nickname,
            "status": status,
        },
    )


async def emit_online_users(sio) -> None:
    await sio.emit("online_users", {"users": await list_online()})


async def update_status_and_emit(
    sio,
    *,
    user_id: str,
    status: str,
    nickname: str | None = None,
    emit_snapshot: bool = True,
) -> None:
    await update_status(user_id=user_id, status=status)

    info = await get_user_info(user_id)
    resolved_nickname = nickname or str((info or {}).get("nickname") or "")
    if not resolved_nickname:
        return

    await emit_user_status_changed(
        sio,
        user_id=user_id,
        nickname=resolved_nickname,
        status=status,
    )
    if emit_snapshot:
        await emit_online_users(sio)


async def set_online_and_emit(
    sio,
    *,
    user_id: str,
    nickname: str,
    status: str = "lobby",
    emit_snapshot: bool = True,
) -> None:
    await set_online(user_id=user_id, nickname=nickname, status=status)
    await emit_user_status_changed(
        sio,
        user_id=user_id,
        nickname=nickname,
        status=status,
    )
    if emit_snapshot:
        await emit_online_users(sio)


async def set_offline_and_emit(
    sio,
    *,
    user_id: str,
    nickname: str,
    emit_snapshot: bool = True,
) -> None:
    await set_offline(user_id=user_id)
    await emit_user_status_changed(
        sio,
        user_id=user_id,
        nickname=nickname,
        status="offline",
    )
    if emit_snapshot:
        await emit_online_users(sio)


async def list_online() -> list[dict[str, Any]]:
    redis = get_redis()
    users_data = await redis.hvals(_PRESENCE_KEY)
    return [json.loads(user) for user in users_data]


async def get_user_status(user_id: str) -> str | None:
    redis = get_redis()
    raw = await redis.hget(_PRESENCE_KEY, user_id)
    if raw is None:
        return None
    data = json.loads(raw)
    return data.get("status")


async def get_user_info(user_id: str) -> dict[str, Any] | None:
    redis = get_redis()
    raw = await redis.hget(_PRESENCE_KEY, user_id)
    if raw is None:
        return None
    return json.loads(raw)
