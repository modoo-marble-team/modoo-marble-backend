from __future__ import annotations

import json
from typing import Any

from app.redis_client import get_redis

_PRESENCE_KEY = "modoo:online_users"


async def set_online(*, user_id: str, nickname: str, status: str = "online") -> None:
    redis = await get_redis()
    data = json.dumps({"id": user_id, "nickname": nickname, "status": status})
    await redis.hset(_PRESENCE_KEY, user_id, data)


async def set_offline(*, user_id: str) -> None:
    redis = await get_redis()
    await redis.hdel(_PRESENCE_KEY, user_id)


async def list_online() -> list[dict[str, Any]]:
    redis = await get_redis()
    users_data = await redis.hvals(_PRESENCE_KEY)
    return [json.loads(user) for user in users_data]
