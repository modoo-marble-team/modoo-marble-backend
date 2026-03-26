from __future__ import annotations

import asyncio
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
from app.presence import set_offline_and_emit, set_online_and_emit
from app.redis_client import close_redis, init_redis
from app.routers import auth, game, lobby, users
from app.utils.jwt import decode_token

logger = structlog.get_logger()

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.CORS_ORIGINS,
)

_sid_to_user: dict[str, int] = {}
_room_disconnect_tasks: dict[int, asyncio.Task[None]] = {}

register_game_handlers(sio, _sid_to_user)
register_lobby_handlers(sio, _sid_to_user)
register_dm_handlers(sio, _sid_to_user)


def _cancel_room_disconnect_cleanup(user_id: int) -> None:
    task = _room_disconnect_tasks.pop(user_id, None)
    if task is not None:
        task.cancel()


def _schedule_room_disconnect_cleanup(user_id: int) -> None:
    if user_id in _room_disconnect_tasks:
        return

    async def _cleanup() -> None:
        try:
            await _handle_room_disconnect(user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("room disconnect cleanup task 실패", user_id=user_id)
        finally:
            _room_disconnect_tasks.pop(user_id, None)

    _room_disconnect_tasks[user_id] = asyncio.create_task(_cleanup())


@sio.event
async def connect(sid: str, environ: dict, auth_data: dict | None):
    try:
        token = (auth_data or {}).get("token")
        if not token:
            raise ConnectionRefusedError("토큰이 없습니다.")

        payload = decode_token(
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            token=str(token),
        )
        sub = payload.get("sub")
        if not sub:
            raise ConnectionRefusedError("토큰 정보가 올바르지 않습니다.")

        try:
            user_id = int(sub)
        except (TypeError, ValueError) as e:
            raise ConnectionRefusedError("사용자 정보가 올바르지 않습니다.") from e

        user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
        if not user:
            raise ConnectionRefusedError("사용자를 찾을 수 없습니다.")

        _cancel_room_disconnect_cleanup(int(user.id))
        _sid_to_user[sid] = int(user.id)
        await handle_game_socket_connect(sid=sid, user_id=int(user.id))

        await set_online_and_emit(
            sio,
            user_id=str(user.id),
            nickname=user.nickname,
            status="lobby",
        )
        await sio.enter_room(sid, f"user:{user.id}")
        return True
    except ConnectionRefusedError as e:
        raise e
    except Exception:
        raise ConnectionRefusedError("서버 내부 오류가 발생했습니다.")


@sio.event
async def disconnect(sid: str):
    user_id = _sid_to_user.get(sid)

    try:
        if user_id is not None:
            user = await User.get_or_none(id=int(user_id), deleted_at__isnull=True)
            nickname = user.nickname if user else ""

            await handle_game_socket_disconnect(sid=sid, user_id=int(user_id))
            _sid_to_user.pop(sid, None)

            has_other_socket = any(
                mapped_user_id == int(user_id)
                for mapped_user_id in _sid_to_user.values()
            )
            if not has_other_socket:
                _schedule_room_disconnect_cleanup(int(user_id))
                await set_offline_and_emit(
                    sio,
                    user_id=str(user_id),
                    nickname=nickname,
                )
    except Exception:
        pass
    finally:
        _sid_to_user.pop(sid, None)


async def _handle_room_disconnect(user_id: int) -> None:
    """소켓 끊김 시 대기방 멤버십 정리."""
    from app.services.room_service import RoomService

    room_service = RoomService()
    try:
        room_id = await room_service._get_user_room_id(user_id)
        if room_id is None:
            logger.info("room_disconnect_no_room", user_id=user_id)
            return

        room = await room_service.get_room(room_id)
        if room is None:
            logger.warning(
                "room_disconnect_room_missing_in_redis",
                user_id=user_id,
                room_id=room_id,
            )
            return
        if room.get("status") != "waiting":
            logger.warning(
                "room_disconnect_skip_non_waiting",
                user_id=user_id,
                room_id=room_id,
                room_status=room.get("status"),
            )
            return

        logger.info("room_disconnect_proceeding", user_id=user_id, room_id=room_id)

        room, new_host_id = await room_service.leave_room(
            room_id=room_id, user_id=user_id
        )

        if room is None:
            await sio.emit(
                "lobby_updated", {"action": "removed", "room": {"id": room_id}}
            )
        else:
            await sio.emit(
                "lobby_updated",
                {"action": "updated", "room": room_service.room_card(room)},
            )
            await sio.emit(
                "room_updated",
                room_service.room_snapshot(room),
                room=f"room:{room_id}",
            )
            if new_host_id:
                new_host = next(
                    (p for p in room["players"] if p["id"] == new_host_id), None
                )
                if new_host:
                    await sio.emit(
                        "host_changed",
                        {
                            "new_host_id": new_host_id,
                            "new_host_nickname": new_host["nickname"],
                        },
                        room=f"room:{room_id}",
                    )
    except Exception:
        logger.warning("room disconnect 처리 실패", user_id=user_id)


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
