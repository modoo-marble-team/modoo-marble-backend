from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgres://modoo:modoo1234@db:5432/modoo_marble"
    REDIS_URL: str = "redis://redis:6379"

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24
    JWT_ACCESS_EXPIRE_MINUTES: int = 60

    KAKAO_CLIENT_ID: str = ""
    KAKAO_CLIENT_SECRET: str = ""
    KAKAO_REDIRECT_URI: str = ""
    FRONTEND_LOGIN_REDIRECT: str = ""

    KAKAO_TOKEN_URL: str = "https://kauth.kakao.com/oauth/token"
    KAKAO_ME_URL: str = "https://kapi.kakao.com/v2/user/me"

    GEMINI_API_KEY: str = ""

    APP_ENV: str = "development"
    DEBUG: bool = True
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    GAME_SYNC_DISCONNECT_GRACE_SECONDS: int = 30
    GAME_SYNC_TIMER_CLAIM_TTL_SECONDS: int = 3
    GAME_SYNC_DISCONNECT_SCHEDULE_SHARDS: int = 16
    GAME_SYNC_WORKER_POLL_INTERVAL_SECONDS: int = 1
    GAME_SYNC_WORKER_BATCH_SIZE: int = 100
    GAME_SYNC_WORKER_CONCURRENCY: int = 20
    GAME_SYNC_WORKER_COUNT: int = 10
    GAME_SYNC_LEADER_TTL_SECONDS: int = 5
    GAME_SYNC_PATCH_KEEP_COUNT: int = 200


settings = Settings()

if not settings.JWT_SECRET:
    raise ValueError("JWT_SECRET is required")


TORTOISE_ORM = {
    "connections": {
        "default": settings.DATABASE_URL,
    },
    "apps": {
        "models": {
            "models": [
                "app.models.user",
                "app.models.game",
                "app.models.user_game",
                "aerich.models",
            ],
            "default_connection": "default",
        },
    },
}
JWT_REFRESH_EXPIRE_DAYS: int = 14

REFRESH_COOKIE_NAME: str = "modoo_refresh_token"
REFRESH_COOKIE_SECURE: bool = False
REFRESH_COOKIE_SAMESITE: str = "lax"
REFRESH_COOKIE_PATH: str = "/api/auth"
REFRESH_COOKIE_DOMAIN: str = ""
