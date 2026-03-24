from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UpdateNicknameRequest(BaseModel):
    nickname: str = Field(..., min_length=2, max_length=10)


class CurrentUserContextResponse(BaseModel):
    room_id: str | None
    room_title: str | None
    room_status: str | None
    game_id: str | None
    presence_status: str | None
    resume_target: Literal["lobby", "room", "game"]


class UserStatsResponse(BaseModel):
    total_games: int
    wins: int
    losses: int


class UserMeResponse(BaseModel):
    id: int
    nickname: str
    profile_image_url: str | None = None
    is_guest: bool
    stats: UserStatsResponse


class UpdateNicknameResponse(BaseModel):
    id: int
    nickname: str
