"""
redis_client.py — Redis 비동기 클라이언트 싱글턴
게임 중 실시간 상태 관리에 사용.
"""

import redis.asyncio as aioredis

from app.config import settings

redis_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """Redis 연결 초기화."""
    global redis_client
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )
    await redis_client.ping()
    return redis_client


async def close_redis() -> None:
    """Redis 연결 종료."""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


def get_redis() -> aioredis.Redis:
    """현재 Redis 클라이언트 반환."""
    if redis_client is None:
        raise RuntimeError("Redis 클라이언트가 초기화되지 않았습니다.")
    return redis_client
