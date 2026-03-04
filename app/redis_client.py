import redis.asyncio as aioredis

from app.config import settings

# Redis 클라이언트 싱글턴
redis_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """서버 시작 시 Redis 연결 초기화."""
    global redis_client
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,  # bytes -> str로 변환
    )
    await redis_client.ping()  # 연결 확인
    return redis_client


async def close_redis() -> None:
    """서버 종료 시 Redis 연결 정리."""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


def get_redis() -> aioredis.Redis:
    """현재 Redis 클라이언트 반환. 라우터에서 의존성 주입으로 사용"""
    if redis_client is None:
        raise RuntimeError("Redis 클라이언트가 초기화되지 않았습니다.")
    return redis_client
