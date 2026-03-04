from contextlib import asynccontextmanager

import socketio
import structlog
from fastapi import FastAPI
from fastapi.openapi.models import OAuth2
from starlette.middleware.cors import CORSMiddleware
from tortoise import Tortoise

from app.config import TORTOISE_ORM, settings
from app.redis_client import close_redis, init_redis
from app.routers import auth, game, lobby, users

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


# 서버 시작/종료 시 실행할 작업
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시
    await Tortoise.init(config=TORTOISE_ORM)  # DB 연결
    await Tortoise.generate_schemas()  # 모델을 보고 DB에 테이블을 자동으로 생성
    await init_redis()  # Redis 연결
    logger.info("✅ DB/Redis 연결이 완료되었습니다.")

    yield  # 연결 유지(서버 실행 중)
    # 서버가 살아있고 DB랑 Redis 연결이 유지되도록 도와줌

    # 종료 시 (연결 정리)
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
# app.include_router(users.router, prefix="/v1/users", tags=["Users"])
# app.include_router(lobby.router, prefix="/v1/rooms", tags=["Lobby"])
# app.include_router(game.router, prefix="/v1/game", tags=["Game"])


# 헬스체크
@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "title": app.title, "version": app.version}


# Socket.IO 마운트
socket_app = socketio.ASGIApp(
    sio, app
)  # 둘을 하나로 합침. uvicorn이 하나의 앱만 실행할 수 있기 때문
application = socket_app  # 최종적으로 application이라는 변수에 담음. 이후 docker에서 application이라는 이름으로 실행 명령어를 내릴 것.
