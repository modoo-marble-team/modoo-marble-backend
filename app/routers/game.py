from __future__ import annotations

from fastapi import APIRouter, Depends

from app.errors import ApiError
from app.game.presentation import serialize_game_snapshot
from app.game.state import get_game_state
from app.utils.auth_dep import AuthUser, get_auth_user

router = APIRouter()


@router.get("/games/{game_id}")
async def get_game(game_id: str, _: AuthUser = Depends(get_auth_user)) -> dict:
    state = await get_game_state(game_id)
    if state is None:
        raise ApiError(
            status_code=404,
            code="GAME_NOT_FOUND",
            message="게임을 찾을 수 없습니다.",
        )
    return serialize_game_snapshot(state)
