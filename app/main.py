from __future__ import annotations

from contextlib import asynccontextmanager

import socketio
import structlog
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.middleware.cors import CORSMiddleware
from tortoise import Tortoise

from app.config import TORTOISE_ORM, settings
from app.dm.socket_handlers import register_dm_handlers
from app.errors import register_error_handlers
from app.game.socket_handlers import register_game_handlers
from app.game.sync_runtime import (
    handle_game_socket_connect,
    handle_game_socket_disconnect,
    start_game_sync_scheduler,
    stop_game_sync_scheduler,
)
from app.lobby.socket_handlers import register_lobby_handlers
from app.models.user import User
from app.presence import set_offline, set_online
from app.redis_client import close_redis, init_redis
from app.routers import auth, game, lobby, users
from app.utils.jwt import decode_token

logger = structlog.get_logger()

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.CORS_ORIGINS,
)
_sid_to_user: dict[str, int] = {}
register_game_handlers(sio, _sid_to_user)
register_lobby_handlers(sio, _sid_to_user)
register_dm_handlers(sio, _sid_to_user)


async def _broadcast_user_status(user_id: int, nickname: str, status: str) -> None:
    await sio.emit(
        "user_status_changed",
        {
            "id": user_id,
            "nickname": nickname,
            "status": status,
        },
    )


@sio.event
async def connect(sid: str, environ: dict, auth_data: dict | None):
    try:
        token = (auth_data or {}).get("token")
        if not token:
            raise ConnectionRefusedError("Missing token")

        payload = decode_token(
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            token=str(token),
        )
        sub = payload.get("sub")
        if not sub:
            raise ConnectionRefusedError("Invalid payload")

        try:
            user_id = int(sub)
        except (TypeError, ValueError) as e:
            raise ConnectionRefusedError("Invalid user id") from e

        user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
        if not user:
            raise ConnectionRefusedError("User not found or deleted")

        _sid_to_user[sid] = int(user.id)
        await handle_game_socket_connect(sid=sid, user_id=int(user.id))
        await set_online(user_id=str(user.id), nickname=user.nickname, status="online")
        await set_online(user_id=str(user.id), nickname=user.nickname, status="lobby")

        await sio.enter_room(sid, f"user:{user.id}")
        await _broadcast_user_status(int(user.id), user.nickname, "lobby")
        return True
    except ConnectionRefusedError as e:
        raise e
    except Exception:
        raise ConnectionRefusedError("Internal server error")


@sio.event
async def disconnect(sid: str):
    user_id = _sid_to_user.get(sid)

    try:
        if user_id is not None:
            user = await User.get_or_none(id=int(user_id), deleted_at__isnull=True)
            nickname = user.nickname if user else ""

            await handle_game_socket_disconnect(sid=sid, user_id=int(user_id))
            await set_offline(user_id=str(user_id))
            await _broadcast_user_status(int(user_id), nickname, "offline")
    except Exception:
        pass
    finally:
        _sid_to_user.pop(sid, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await Tortoise.init(config=TORTOISE_ORM)
    if settings.APP_ENV == "development":
        await Tortoise.generate_schemas()

    await init_redis()
    await start_game_sync_scheduler()
    logger.info("✅ DB/Redis 연결이 완료되었습니다.")

    yield

    await stop_game_sync_scheduler()
    await auth.http_client.aclose()
    await close_redis()
    await Tortoise.close_connections()
    logger.info("☠️️☠️️☠️️ DB/Redis 연결이 끊겼습니다.️")


app = FastAPI(
    title="모두의마블 API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.state.sio = sio
app.state.sid_to_user = _sid_to_user
register_error_handlers(app)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )

    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }

    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.setdefault("security", [{"BearerAuth": []}])

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(users.router, prefix="/api", tags=["Users"])
app.include_router(lobby.router, prefix="/api", tags=["Lobby"])
app.include_router(game.router, prefix="/api", tags=["Game"])


@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "title": app.title, "version": app.version}


@app.get("/api/health", tags=["System"])
async def api_health_check():
    return {"status": "ok", "title": app.title, "version": app.version}


socket_app = socketio.ASGIApp(sio, app)
application = socket_app