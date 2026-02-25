COMPOSE_FILE ?= docker-compose.local.yml   # 기본 compose 파일 (override 가능)
APP_MODULE = main:app                     # FastAPI ASGI 앱 경로
HOST = 0.0.0.0
PORT = 8000

PYTHON = .venv/bin/python
UVICORN = .venv/bin/uvicorn
PYTEST = .venv/bin/pytest
BLACK = .venv/bin/black
ISORT = .venv/bin/isort
MYPY = .venv/bin/mypy
AERICH = .venv/bin/aerich

.PHONY: venv install sync run prod migrate upgrade downgrade format test build up down restart logs ps dmigrate dupgrade fetch checkout-develop pull-develop sync-develop check

venv:                                      # 가상환경 생성
	uv venv

install:                                   # requirements 설치
	uv pip install -r requirements.txt

run:                                       # 개발 서버 실행 (reload)
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

prod:                                      # 프로덕션 실행 (no reload)
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT)

migrate:                                   # migration 파일 생성
	$(AERICH) migrate

upgrade:                                   # migration 적용
	$(AERICH) upgrade

downgrade:                                 # 마지막 migration 롤백
	$(AERICH) downgrade

format:                                    # 코드 포맷 정렬
	$(BLACK) .
	$(ISORT) .

test:                                      # 타입체크 + pytest + coverage
	$(MYPY) . || true
	@if [ -z "$(ARGS)" ]; then \
		$(PYTEST) --cov=. --cov-report=term-missing --cov-report=html; \
	else \
		$(PYTEST) $(ARGS) --cov=$(ARGS) --cov-report=term-missing --cov-report=html; \
	fi

build:                                     # 도커 이미지 빌드
	docker compose -f $(COMPOSE_FILE) build $(ARGS)

up:                                        # 도커 컨테이너 실행
	docker compose -f $(COMPOSE_FILE) up -d $(ARGS)

down:                                      # 도커 종료
	docker compose -f $(COMPOSE_FILE) down

restart:                                   # 도커 재시작
	docker compose -f $(COMPOSE_FILE) restart

logs:                                      # 실시간 로그 확인
	docker compose -f $(COMPOSE_FILE) logs -f --tail=100 $(ARGS)

ps:                                        # 실행 중 컨테이너 목록
	docker compose -f $(COMPOSE_FILE) ps

dmigrate:                                  # 도커 내부 migration 생성
	docker compose -f $(COMPOSE_FILE) exec backend aerich migrate

dupgrade:                                  # 도커 내부 migration 적용
	docker compose -f $(COMPOSE_FILE) exec backend aerich upgrade

sync:                              # fetch + checkout develop + pull
	git fetch origin
	git checkout develop
	git pull origin develop

check: format test                         # 전체 품질 검사 