"""
constants.py — 게임 상수 정의
"""

# ── 게임 기본 설정 ─────────────────────────────────────────
INITIAL_BALANCE = 1_000_000_000  # 초기 자본금 10억원
MAX_ROUND = 20  # 최대 라운드 수
MAX_PLAYERS = 4  # 최대 플레이어 수
MIN_PLAYERS = 2  # 최소 플레이어 수
TURN_TIMEOUT_SEC = 30  # 턴 타임아웃 (초)
RECONNECT_TIMEOUT_SEC = 30  # 재연결 대기 시간 (초)
BOARD_SIZE = 32  # 보드 칸 수
GO_SALARY = 200_000_000  # 출발점 통과 급여 2억원
SELL_RATIO = 0.5  # 매각 시 환급 비율 (50%)

# ── 건물 단계 ──────────────────────────────────────────────
BUILDING_LEVELS = {
    0: "빈 땅",
    1: "집 1채",
    2: "집 2채",
    3: "집 3채",
    4: "호텔",
    5: "랜드마크",
}

# ── 방 상태 ────────────────────────────────────────────────
ROOM_STATUS_WAITING = "waiting"
ROOM_STATUS_PLAYING = "playing"
ROOM_STATUS_FINISHED = "finished"

# ── 게임 종료 사유 ─────────────────────────────────────────
END_REASON_BANKRUPT = "bankrupt"
END_REASON_ROUND_LIMIT = "round_limit"
