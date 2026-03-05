# ==============================================================================
# Makefile
# ==============================================================================

COMPOSE_FILE ?= docker-compose.local.yml

UV ?= uv
RUN = $(UV) run

.PHONY: fmt lint test check build up down restart logs ps git-sync

# ------------------------------------------------------------------------------
# Code Quality
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
# One-shot
# ------------------------------------------------------------------------------

check: fmt lint test  # 포맷 + 린트 + 테스트 한번에

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

# ------------------------------------------------------------------------------
# Git Helpers
# ------------------------------------------------------------------------------

sync: # develop 최신화
	git checkout develop
	git fetch origin
	git pull origin develop