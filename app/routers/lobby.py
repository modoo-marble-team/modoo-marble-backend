"""
lobby.py — 로비 API 라우터
LOBBY-001 ~ LOBBY-006: 방 목록, 생성, 입장, 퇴장, 준비, 시작
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("", summary="방 목록 조회 (LOBBY-001)")
async def list_rooms():
    """로비 방 목록 반환. 필터: status, exclude_private, keyword."""
    # TODO: 구현 예정
    return {"rooms": [], "total": 0}


@router.post("", summary="방 생성 (LOBBY-002)")
async def create_room():
    """새 방 생성. 비밀방 시 password 필수."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.post("/{room_id}/join", summary="방 입장 (LOBBY-003)")
async def join_room(room_id: str):
    """방에 입장. 비밀방이면 password 필요."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.post("/{room_id}/leave", summary="방 퇴장 (LOBBY-004)")
async def leave_room(room_id: str):
    """방에서 나감. 방장 퇴장 시 위임 규칙 적용."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.patch("/{room_id}/ready", summary="준비 상태 토글 (LOBBY-005)")
async def toggle_ready(room_id: str):
    """준비 상태 토글 (is_ready: false ↔ true)."""
    # TODO: 구현 예정
    return {"message": "not implemented"}


@router.post("/{room_id}/start", summary="게임 시작 (LOBBY-006)")
async def start_game(room_id: str):
    """게임 시작. 방장만, 최소 2명 + 전원 준비완료."""
    # TODO: 구현 예정
    return {"message": "not implemented"}
