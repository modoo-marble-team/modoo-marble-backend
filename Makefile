# ==============================================================================
# Makefile (uv + FastAPI + Tortoise/Aerich + Docker Compose)
#
# ✅ 공용 사용 규칙
# - 의존성 설치/실행은 `uv` 기준 (pyproject.toml + uv.lock)
# - 로컬 개발: make sync → make run
# - 프로덕션(배포): make prod (gunicorn + UvicornWorker)
# - DB 마이그레이션: make migrate/upgrade/downgrade (aerich)
# - 도커: make up/down/logs/build 등
# ==============================================================================

# 기본 docker compose 파일 (필요하면 override 가능)
# 예) make up COMPOSE_FILE=docker-compose.prod.yml
COMPOSE_FILE ?= docker-compose.local.yml

# ASGI 앱 경로 (FastAPI/ASGI app 객체 위치)
# 기본값: 루트 main.py에 `app = FastAPI()`가 있을 때 main:app
# 예) app/main.py로 옮기면 APP_MODULE=app.main:app 로 변경
APP_MODULE ?= main:app

# 서버 바인딩
HOST ?= 0.0.0.0
PORT ?= 8000

# gunicorn 워커 수 (배포 시 사용)
# 예) make prod WORKERS=4
WORKERS ?= 2

# uv 실행 커맨드
UV ?= uv
RUN = $(UV) run

.PHONY: venv sync run prod migrate upgrade downgrade fmt lint test build up down restart logs ps dmigrate dupgrade git-sync check

# ------------------------------------------------------------------------------
# Python / Dependencies
# ------------------------------------------------------------------------------

venv:  # 가상환경 생성(uv venv)
	$(UV) venv

sync:  # 의존성 설치/동기화 (pyproject.toml + uv.lock 기준)
	$(UV) sync

# ------------------------------------------------------------------------------
# App Run
# ------------------------------------------------------------------------------

run:  # 개발 서버 실행 (auto-reload)
	$(RUN) uvicorn $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

prod:  # 프로덕션 실행 (gunicorn + UvicornWorker, no reload)
	gunicorn -k uvicorn.workers.UvicornWorker -w $(WORKERS) -b $(HOST):$(PORT) $(APP_MODULE)

# ------------------------------------------------------------------------------
# DB Migration (Aerich)
# ------------------------------------------------------------------------------

migrate:  # migration 파일 생성
	$(RUN) aerich migrate

upgrade:  # migration 적용
	$(RUN) aerich upgrade

downgrade:  # 마지막 migration 롤백
	$(RUN) aerich downgrade

# ------------------------------------------------------------------------------
# Code Quality (Ruff only)
# ------------------------------------------------------------------------------

fmt:  # 포맷 + import 정리(자동 수정 포함)
	$(RUN) ruff format .
	$(RUN) ruff check . --fix

lint:  # 린트만(수정 없음)
	$(RUN) ruff check .

# ------------------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------------------

test:  # 테스트 실행 (ARGS 지정 시 해당 경로/옵션만 실행)
	@if [ -z "$(ARGS)" ]; then \
		$(RUN) pytest; \
	else \
		$(RUN) pytest $(ARGS); \
	fi

# ------------------------------------------------------------------------------
# Docker Compose
# ------------------------------------------------------------------------------

build:  # 도커 이미지 빌드 (ARGS로 서비스 지정 가능)
	docker compose -f $(COMPOSE_FILE) build $(ARGS)

up:  # 도커 컨테이너 실행 (백그라운드)
	docker compose -f $(COMPOSE_FILE) up -d $(ARGS)

down:  # 도커 컨테이너 종료/삭제
	docker compose -f $(COMPOSE_FILE) down

restart:  # 도커 컨테이너 재시작
	docker compose -f $(COMPOSE_FILE) restart

logs:  # 도커 로그 tail (ARGS로 서비스 지정 가능)
	docker compose -f $(COMPOSE_FILE) logs -f --tail=100 $(ARGS)

ps:  # 실행 중 컨테이너 목록
	docker compose -f $(COMPOSE_FILE) ps

dmigrate:  # 도커 내부에서 migration 파일 생성 (backend 컨테이너 기준)
	docker compose -f $(COMPOSE_FILE) exec backend $(UV) run aerich migrate

dupgrade:  # 도커 내부에서 migration 적용 (backend 컨테이너 기준)
	docker compose -f $(COMPOSE_FILE) exec backend $(UV) run aerich upgrade

# ------------------------------------------------------------------------------
# Git Helpers
# ------------------------------------------------------------------------------

git-sync: # 브랜치 최신화
	git checkout develop
	git fetch origin
	git pull origin develop

# ------------------------------------------------------------------------------
# One-shot
# ------------------------------------------------------------------------------

check: fmt test  # 포맷/린트 + 테스트 한번에