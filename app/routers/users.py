"""
users.py — 유저 API 라우터
AUTH-003: 닉네임 설정, USER-001: 프로필 조회
"""

from fastapi import APIRouter

router = APIRouter()


@router.patch("/me/nickname", summary="닉네임 설정 (AUTH-003)")
async def set_nickname():
    """신규 가입 후 닉네임 설정."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.get("/me", summary="내 프로필 조회 (USER-001)")
async def get_my_profile():
    """로그인한 유저의 프로필 + 전적 반환."""
    # TODO: 구현 예정
    return {"message": "not implemented"}
