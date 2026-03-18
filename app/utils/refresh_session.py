from __future__ import annotations

from app.redis_client import get_redis

_REFRESH_PREFIX = "modoo:refresh:"


def _key(jti: str) -> str:
    return f"{_REFRESH_PREFIX}{jti}"


async def save_refresh_session(*, jti: str, user_id: int, ttl_seconds: int) -> None:
    redis = await get_redis()
    await redis.set(_key(jti), str(user_id), ex=ttl_seconds)


async def get_refresh_session(jti: str) -> str | None:
    redis = await get_redis()
    return await redis.get(_key(jti))


async def delete_refresh_session(jti: str) -> None:
    redis = await get_redis()
    await redis.delete(_key(jti))
