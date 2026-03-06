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

    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24
    JWT_ACCESS_EXPIRE_MINUTES: int = 60

    KAKAO_CLIENT_ID: str = ""
    KAKAO_CLIENT_SECRET: str = ""
    KAKAO_REDIRECT_URI: str = ""
    FRONTEND_LOGIN_REDIRECT: str = ""

    GEMINI_API_KEY: str = ""

    APP_ENV: str = "development"
    DEBUG: bool = True
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]


settings = Settings()

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
