"""
game.py — 게임 API 라우터
GAME-001 ~ GAME-004: 땅 구매, 건설, 매각, 상태 조회
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/{room_id}/state", summary="게임 상태 조회 (GAME-004)")
async def get_game_state(room_id: str):
    """재연결 시 게임 상태 복원용."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.post("/{room_id}/buy", summary="땅 구매 (GAME-001)")
async def buy_tile(room_id: str):
    """현재 위치한 미소유 땅 구매."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.post("/{room_id}/build", summary="건물 건설 (GAME-002)")
async def build_on_tile(room_id: str):
    """소유한 땅에 건물 건설 (집 → 호텔 → 랜드마크)."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.post("/{room_id}/sell", summary="건물/땅 매각 (GAME-003)")
async def sell_property(room_id: str):
    """소유한 건물 또는 땅 매각."""
    # TODO: 구현 예정
    return {"message": "not implemented"}
