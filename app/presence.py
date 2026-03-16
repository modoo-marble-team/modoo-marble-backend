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
    """이미 온라인인 유저의 상태만 업데이트 (lobby / in_room / playing)."""
    redis = get_redis()
    raw = await redis.hget(_PRESENCE_KEY, user_id)
    if raw is None:
        return
    data = json.loads(raw)
    data["status"] = status
    await redis.hset(_PRESENCE_KEY, user_id, json.dumps(data))


async def set_offline(*, user_id: str) -> None:
    redis = get_redis()
    await redis.hdel(_PRESENCE_KEY, user_id)


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
