# 🎲 modoo-marble — Backend

온라인 2D 웹 보드게임 **모두의 마블** 백엔드 서버입니다.
FastAPI + python-socketio 기반의 실시간 멀티플레이어 게임 서버입니다.

---

## 📦 Tech Stack

| 분류 | 기술 |
|---|---|
| 웹 프레임워크 | FastAPI + Uvicorn (ASGI) |
| 실시간 통신 | python-socketio (ASGI 마운트) |
| ORM | Tortoise ORM + Aerich (마이그레이션) |
| DB | PostgreSQL 16 |
| 캐시 / 게임 상태 | Redis + aioredis |
| AI | Google Gemini API |
| Python | 3.11 이상 |

---

## 📁 프로젝트 구조

```
modoo-marble-backend/        # 레포 루트
├── main.py                  # FastAPI 앱 생성, socketio 마운트, 라우터 등록
├── constants.py             # 게임 수치 상수 (타일 데이터, 금액, 타임아웃 등)
├── config.py                # 환경 변수 로드 (pydantic-settings)
│
├── routers/                 # REST API 라우터
│   ├── auth.py              # 카카오 로그인, 게스트 로그인, 닉네임 설정
│   ├── users.py             # 마이페이지
│   ├── rooms.py             # 방 생성·입장·퇴장·준비·시작
│   └── game.py              # 구매·건설·매각
│
├── sockets/                 # python-socketio 이벤트 핸들러
│   ├── connection.py        # connect / disconnect
│   ├── lobby.py             # enter_room, leave_room, send_chat
│   ├── game.py              # roll_dice, confirm_penalty, dm_send
│   └── emitters.py          # 공통 emit 헬퍼 함수 모음
│
├── services/                # 비즈니스 로직
│   ├── auth_service.py
│   ├── room_service.py
│   ├── game_service.py      # 이동, 구매, 건설, 파산, 무인도 등
│   ├── ai_service.py        # Gemini API 호출, fallback 처리
│   └── redis_service.py     # Redis 게임 상태 read/write
│
├── models/                  # Tortoise ORM 모델
│   ├── user.py
│   ├── room.py
│   └── game.py
│
├── schemas/                 # Pydantic 요청/응답 스키마
│   ├── auth.py
│   ├── room.py
│   └── game.py
│
└── utils/                   # 공통 유틸
    ├── jwt.py               # JWT 발급·검증
    └── exceptions.py        # 공통 예외 클래스
```

---

## ⚙️ 로컬 개발 환경 설정

### 1. 사전 준비

- Python 3.11+
- PostgreSQL 16
- Redis 7+

### 2. 저장소 클론 및 가상환경 설정

```bash
git clone https://github.com/{org}/modoo-marble-backend.git
cd modoo-marble-backend

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 3. 환경 변수 설정

`.env.example`을 복사하여 `.env`를 생성하고 값을 채웁니다.

```bash
cp .env.example .env
```

```dotenv
# .env.example
DATABASE_URL=postgres://user:password@localhost:5432/modoo_marble
REDIS_URL=redis://localhost:6379
SECRET_KEY=your-jwt-secret-key
KAKAO_CLIENT_ID=your-kakao-rest-api-key
GEMINI_API_KEY=your-gemini-api-key
```

### 4. DB 마이그레이션 및 서버 실행

```bash
# 최초 마이그레이션 초기화 (최초 1회)
aerich init -t config.TORTOISE_ORM
aerich init-db

# 마이그레이션 적용 (모델 변경 후)
aerich migrate --name "설명"
aerich upgrade

# 개발 서버 실행
uvicorn main:app --reload --port 8000
```

---

## 🔑 주요 환경 변수

| 변수명 | 설명 |
|---|---|
| `DATABASE_URL` | PostgreSQL 접속 URL |
| `REDIS_URL` | Redis 접속 URL |
| `SECRET_KEY` | JWT 서명 키 (충분한 길이의 랜덤값) |
| `KAKAO_CLIENT_ID` | 카카오 REST API 키 |
| `GEMINI_API_KEY` | Google Gemini API 키 |

---

## 📡 API 문서

서버 실행 후 아래에서 확인할 수 있습니다.

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

소켓 이벤트 명세는 팀 공유 폴더의 **`모두의마블_API명세서_v2.xlsx`** 를 참고하세요.

---

## 👥 팀

BE 3인 / FE 3인 — 4주 MVP 스프린트
요구사항 정의서 v7 기준 구현
