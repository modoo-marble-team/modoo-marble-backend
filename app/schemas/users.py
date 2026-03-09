from __future__ import annotations

from pydantic import BaseModel, Field


class UpdateNicknameRequest(BaseModel):
    nickname: str = Field(..., min_length=2, max_length=10)
