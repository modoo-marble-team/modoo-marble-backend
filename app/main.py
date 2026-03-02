"""
main.py — FastAPI 앱 엔트리포인트
python-socketio ASGI 마운트 + Tortoise ORM lifespan 관리 + Swagger UI
"""

from contextlib import asynccontextmanager

import socketio
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tortoise import Tortoise

from app.config import TORTOISE_ORM, settings
from app.redis_client import close_redis, init_redis
from app.routers import auth, game, lobby, users

logger = structlog.get_logger()

# ── Socket.IO 서버 ────────────────────────────────────────
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.CORS_ORIGINS,
    logger=False,
    engineio_logger=False,
)


# ── Lifespan (DB + Redis 초기화/종료) ─────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 서버 시작 — DB/Redis 연결 초기화")
    await Tortoise.init(config=TORTOISE_ORM)
    await Tortoise.generate_schemas()
    await init_redis()
    logger.info("✅ DB/Redis 연결 완료")
    yield
    # Shutdown
    logger.info("🛑 서버 종료 — 연결 정리")
    await close_redis()
    await Tortoise.close_connections()


# ── FastAPI 앱 생성 ───────────────────────────────────────
app = FastAPI(
    title="모두의 마블 API",
    description="모두의 마블 스타일 웹 보드게임 백엔드 API",
    version="0.1.0",
    docs_url="/docs",  # Swagger UI
    redoc_url="/redoc",  # ReDoc
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS 미들웨어 ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST API 라우터 등록 ──────────────────────────────────
app.include_router(auth.router, prefix="/v1/auth", tags=["인증"])
app.include_router(users.router, prefix="/v1/users", tags=["유저"])
app.include_router(lobby.router, prefix="/v1/rooms", tags=["로비"])
app.include_router(game.router, prefix="/v1/game", tags=["게임"])


# ── 헬스체크 엔드포인트 ──────────────────────────────────
@app.get("/health", tags=["시스템"])
async def health_check():
    """서버 상태 확인용 엔드포인트. CI/CD 및 로드밸런서 헬스체크에 사용."""
    return {"status": "ok", "version": app.version}


# ── Socket.IO ASGI 마운트 ─────────────────────────────────
# Socket.IO를 /ws 경로에 마운트 (REST API와 분리)
socket_app = socketio.ASGIApp(sio, app)

# uvicorn이 실행할 최종 ASGI 앱
application = socket_app
