"""
config.py — 환경변수 기반 설정 관리
pydantic-settings를 사용하여 .env 파일에서 설정값을 읽어온다.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ───────────────────────────────────────────
    DATABASE_URL: str = "postgres://modoo:modoo1234@db:5432/modoo_marble"

    # ── Redis ──────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379"

    # ── JWT ────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # ── Kakao OAuth ────────────────────────────────────────
    KAKAO_CLIENT_ID: str = ""
    KAKAO_CLIENT_SECRET: str = ""
    KAKAO_REDIRECT_URI: str = ""

    # ── Gemini AI ──────────────────────────────────────────
    GEMINI_API_KEY: str = ""

    # ── App ────────────────────────────────────────────────
    APP_ENV: str = "development"  # development | staging | production
    DEBUG: bool = True
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]


settings = Settings()

# ── Tortoise ORM 설정 (aerich 호환) ──────────────────────────
TORTOISE_ORM = {
    "connections": {
        "default": settings.DATABASE_URL,
    },
    "apps": {
        "models": {
            "models": ["app.models", "aerich.models"],
            "default_connection": "default",
        },
    },
}
