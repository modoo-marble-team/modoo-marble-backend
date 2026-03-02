"""
auth.py — 인증 API 라우터
AUTH-001: 카카오 콜백, AUTH-002: 게스트 로그인, AUTH-003: 닉네임 설정
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/kakao/callback", summary="카카오 로그인 콜백 (AUTH-001)")
async def kakao_callback():
    """카카오 인가코드 → JWT 발급."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.post("/guest", summary="게스트 로그인 (AUTH-002)")
async def guest_login():
    """게스트 세션 토큰 발급."""
    # TODO: 구현 예정
    return {"message": "not implemented"}
