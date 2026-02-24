# 🤝 Contributing Guide — 모두의 마블 BE

이 문서는 백엔드 팀 내 코드 품질과 협업 일관성을 위한 규칙을 정의합니다.
**모든 팀원은 PR 전 이 문서를 기준으로 코드를 작성해 주세요.**

---

## 1. 코드 스타일 & 포매터

### 사용 도구

| 도구 | 역할 | 설치 |
|---|---|---|
| **Black** | 코드 자동 포맷 | `pip install black` |
| **isort** | import 정렬 | `pip install isort` |
| **Ruff** | 린터 (flake8 대체) | `pip install ruff` |

### 설정 (`pyproject.toml`)

```toml
[tool.black]
line-length = 88
target-version = ["py311"]

[tool.isort]
profile = "black"
line_length = 88

[tool.ruff]
line-length = 88
select = ["E", "F", "I"]   # pycodestyle, pyflakes, isort
ignore = ["E501"]
```

### 실행 방법

```bash
black .          # 전체 포맷
isort .          # import 정렬
ruff check .     # 린트 검사
ruff check . --fix  # 자동 수정 가능한 것만 수정
```

> **PR 전 반드시** `black .` → `isort .` → `ruff check .` 순으로 실행하고 에러 없는 상태로 올려주세요.

### 코드 작성 규칙

- **네이밍은 snake_case**로 통일합니다. (변수, 함수, 파일, DB 컬럼 모두)
- **타입 힌트는 필수**입니다. 함수 파라미터와 반환 타입에 반드시 명시합니다.
  ```python
  # ✅ 올바른 예
  async def get_room(room_id: str) -> dict:

  # ❌ 잘못된 예
  async def get_room(room_id):
  ```
- **비동기 함수는 async/await**를 일관되게 사용합니다. 동기·비동기 혼용 금지.
- **매직 넘버는 `constants.py`** 에 상수로 정의하고 import해서 사용합니다.
  ```python
  # ✅ 올바른 예
  from constants import TURN_TIMEOUT_SECONDS, PASS_GO_SALARY

  # ❌ 잘못된 예
  await asyncio.sleep(30)
  player.balance += 50_000_000
  ```
- **주석**: 복잡한 로직에만 간결하게 작성합니다. 코드로 의도가 명확하다면 주석 불필요.
- **함수 길이**: 한 함수는 한 가지 일만 합니다. 30줄을 넘기면 분리를 고려하세요.

---

## 2. 폴더 구조 & 파일 네이밍

### 규칙

- 모든 파일명은 **snake_case**를 사용합니다.
- 역할에 따라 접미사를 통일합니다.

| 접미사 | 위치 | 예시 |
|---|---|---|
| `_router.py` | `routers/` | `room_router.py` |
| `_service.py` | `services/` | `game_service.py` |
| `_model.py` | `models/` | `user_model.py` |
| `_schema.py` | `schemas/` | `room_schema.py` |
| `_socket.py` | `sockets/` | `game_socket.py` |

### 새 기능 추가 시 파일 생성 순서

```
models/ → schemas/ → services/ → routers/ or sockets/
```

의존 방향: `routers/sockets` → `services` → `models`
서비스 레이어가 라우터의 비즈니스 로직을 직접 담지 않도록 합니다.

---

## 3. Git 브랜치 전략

### 브랜치 구조

```
main          ← 배포용 (직접 push 절대 금지)
└── develop   ← 통합 브랜치 (PR 타겟)
    ├── feature/기능명
    └── fix/버그명
```

### 브랜치 네이밍

| 유형 | 형식 | 예시 |
|---|---|---|
| 기능 개발 | `feature/기능명` | `feature/room-create-api` |
| 버그 수정 | `fix/버그명` | `fix/jail-turn-count-reset` |
| 긴급 수정 | `hotfix/내용` | `hotfix/jwt-expiry` |

- 브랜치명은 **소문자 + 하이픈(-)** 구분을 사용합니다. (언더스코어 X)
- 요구사항 ID를 붙이면 추적이 쉽습니다. (예: `feature/L-001-room-create`)

### 기본 워크플로우

```bash
# 1. develop 최신화 후 브랜치 생성
git checkout develop
git pull origin develop
git checkout -b feature/room-create-api

# 2. 작업 후 커밋
git add 파일명          # git add . 보다 파일 지정 권장
git commit -m "feat: 방 생성 API 구현 (L-001)"

# 3. develop에 PR 생성
git push origin feature/room-create-api
```

> **main 직접 push는 금지**입니다. 반드시 develop → main PR을 통해 병합합니다.

---

## 4. 커밋 메시지 컨벤션

### 형식

```
<타입>: <제목> [(요구사항 ID)]

[본문 - 필요한 경우만]
```

### 타입 목록

| 타입 | 사용 상황 |
|---|---|
| `feat` | 새 기능 추가 |
| `fix` | 버그 수정 |
| `refactor` | 동작 변경 없는 코드 구조 개선 |
| `docs` | 문서, 주석 수정 |
| `chore` | 설정, 의존성, 마이그레이션 |
| `test` | 테스트 추가/수정 |
| `style` | 포맷, 세미콜론 등 로직 변화 없는 수정 |

### 작성 규칙

- 제목은 **50자 이내**, 마침표 없이 작성합니다.
- 제목은 **명령형**으로 작성합니다. ("추가한다" X → "추가" O)
- 요구사항 ID를 괄호 안에 붙이면 추적이 편합니다.

### 예시

```bash
feat: 방 생성 API 구현 (L-001)
fix: 무인도 체류 시 jail_turn_count 초기화 누락 수정 (G-020)
chore: Aerich 마이그레이션 추가 (rooms 테이블)
refactor: send_to_jail() 공통 함수로 추출
docs: CONTRIBUTING.md 브랜치 전략 보완
```

---

## 5. PR 규칙

- PR 타겟은 반드시 **`develop`** 브랜치입니다.
- PR 제목은 커밋 컨벤션과 동일한 형식을 따릅니다.
- **셀프 머지 금지** — 반드시 팀원 1명 이상의 리뷰 후 머지합니다.
- PR 크기는 최대한 작게 유지합니다. (하나의 PR = 하나의 기능 or 수정)
- 머지 전 `black .` + `ruff check .` 통과 확인 필수.

### PR 템플릿 (GitHub `.github/pull_request_template.md` 에 등록 권장)

```markdown
## 작업 내용
-

## 관련 요구사항
-

## 테스트 방법
-

## 체크리스트
- [ ] black / ruff 통과
- [ ] 타입 힌트 추가
- [ ] 매직 넘버 constants.py 처리
```
