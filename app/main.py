from __future__ import annotations

from contextlib import asynccontextmanager

import socketio
import structlog
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.middleware.cors import CORSMiddleware
from tortoise import Tortoise

from app.config import TORTOISE_ORM, settings
from app.models.user import User
from app.presence import list_online, set_offline, set_online
from app.redis_client import close_redis, init_redis
from app.routers import auth, users
from app.utils.jwt import decode_token

# 로깅 라이브러리
# 서버에서 무슨 일이 일어나고 있는지 기록하는 로깅 도구
# 이후 logger.info("✅ DB/Redis 연결 완료") 이렇게 쓸 수 있다.
logger = structlog.get_logger()

# Socket.IO 서버 생성
# AsyncServer 비동기 방식으로 소켓 서버 생성
sio = socketio.AsyncServer(
    async_mode="asgi",  # FastAPI가 ASGI 방식이라 맞춘 것
    cors_allowed_origins=settings.CORS_ORIGINS,  # 허용하는 요청 주소를 정해놓는다
)

_sid_to_user: dict[str, int] = {}


async def _broadcast_online_users() -> None:
    users_list = await list_online()
    await sio.emit("online_users", {"users": users_list})


# Socket 인증 + Presence
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

        user_id = int(sub)
        user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
        if not user:
            raise ConnectionRefusedError("User not found or deleted")

        _sid_to_user[sid] = int(user.id)
        await set_online(user_id=str(user.id), nickname=user.nickname, status="online")

        await sio.enter_room(sid, f"user:{user.id}")
        await _broadcast_online_users()
        return True
    except ConnectionRefusedError as e:
        raise e
    except Exception:
        raise ConnectionRefusedError("Internal server error")


@sio.event
async def disconnect(sid: str):
    try:
        user_id = _sid_to_user.pop(sid, None)
        if user_id is not None:
            await set_offline(user_id=str(user_id))
            await _broadcast_online_users()
    except Exception:
        pass


# 서버 시작/종료 시 실행할 작업
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시
    await Tortoise.init(config=TORTOISE_ORM)  # DB 연결
    if settings.APP_ENV == "development":
        await Tortoise.generate_schemas()  # 개발 환경에서만 테이블 자동 생성
    await init_redis()  # Redis 연결
    logger.info("✅ DB/Redis 연결이 완료되었습니다.")

    yield  # 연결 유지(서버 실행 중)
    # 서버가 살아있고 DB랑 Redis 연결이 유지되도록 도와줌

    # 종료 시 (연결 정리)
    await auth.http_client.aclose()
    await close_redis()
    await Tortoise.close_connections()
    logger.info("☠️️☠️️☠️️ DB/Redis 연결이 끊겼습니다.️")


# FastAPI 앱 생성
app = FastAPI(
    title="모두의마블 API",  # Swagger에 표시될 API 이름
    version="0.1.0",  # API 버전
    docs_url="/docs",  # Swagger UI 주소 (API Test 가능)
    redoc_url="/redoc",  # ReDoc 주소 (읽기 전용)
    lifespan=lifespan,  # 시작/종료 함수 등록
)


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

# CORS 설정
# 허용된 주소의 요청인지 확인하기 위해
app.add_middleware(  # app에 미들웨어 추가 (FastAPI 내장 메서드)
    CORSMiddleware,  # 어떤 미들웨어? > CORS 처리용
    allow_origins=settings.CORS_ORIGINS,  # 허용할 주소 목록 (.env에서 읽어옴)
    allow_credentials=True,  # 쿠키/인증 헤더 허용 (로그인에 필요)
    allow_methods=["*"],  # GET, POST, PATCH, DELETE 등 모든 메서드 허용
    allow_headers=["*"],  # 모든 헤더 허용
)

app.include_router(auth.router, prefix="/v1/auth", tags=["Auth"])
app.include_router(users.router, prefix="/v1", tags=["Users"])

# 헬스체크
@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "title": app.title, "version": app.version}


# Socket.IO 마운트
socket_app = socketio.ASGIApp(
    sio, app
)  # 둘을 하나로 합침. uvicorn이 하나의 앱만 실행할 수 있기 때문
application = socket_app  # 최종적으로 application이라는 변수에 담음. 이후 docker에서 application이라는 이름으로 실행 명령어를 내릴 것.