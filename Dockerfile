# ── 모두의 마블 백엔드 Dockerfile ─────────────────────────
# Multi-stage build: 의존성 설치 → 경량 런타임 이미지

# Stage 1: 의존성 설치
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: 런타임
FROM python:3.12-slim
WORKDIR /app

# 시스템 의존성 (PostgreSQL 클라이언트 등)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 복사
COPY --from=builder /install /usr/local

# 앱 소스코드 복사
COPY . .

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 포트 노출
EXPOSE 8000

# 실행 명령
CMD ["uvicorn", "app.main:application", "--host", "0.0.0.0", "--port", "8000"]
