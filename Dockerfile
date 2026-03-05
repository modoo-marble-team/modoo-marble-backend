# 베이스 이미지 선택
# Docker Hub에서 python:3.11-slim 이미지를 가져옴
# slim: 불필요한 파일 제거된 가벼운 버전
FROM python:3.11-slim

# 컨테이너 안에서 작업할 디렉터리 설정
# 이후 모든 명령어는 /app 안에서 실행됨
WORKDIR /app

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 의존성 파일 먼저 복사 (캐시 활용)
# COPY [로컬 파일] [컨테이너 경로]
COPY pyproject.toml uv.lock ./

# 패키지 설치
RUN uv sync --frozen --no-dev

# 나머지 소스코드 전체 복사
# . = 현재 로컬 디렉터리 전체
COPY . .

# 컨테이너가 8000번 포트를 사용한다고 선언
# 실제로 포트를 여는 게 아니라 "이 포트를 쓸 거야"라고 문서화 하는 것
EXPOSE 8000

# 컨테이너 시작 시 실행할 명령어
# uvicorn으로 app/main.py의 application 객체를 싫행
# CMD는 컨테이너가 시작될 때 실행할 명령어
# uvicorn app.main:application --host 0.0.0.0 --port 8000
CMD ["uv", "run", "uvicorn", "app.main:application", "--host", "0.0.0.0", "--port", "8000"]
# app.main → app/main.py 파일
# :application → 그 파일 안에 있는 application 변수 (Socket.IO + FastAPI 합친 것)
# 0.0.0.0은 "모든 네트워크 인터페이스에서 오는 요청을 받겠다" / localhost만 쓰면 같은 컴퓨터에서 오는 요청만 받아요
# 컨테이너 외부에서 접근하려면 0.0.0.0이 필요
# 8000번 포트로 요청을 받겠다

# 0.0.0.0 > 모든 방향에서 오는 사람 입장 가능
# CORS > 입장한 사람들 신분증 확인
