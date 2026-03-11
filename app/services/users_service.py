from __future__ import annotations

import re

from tortoise.exceptions import IntegrityError

from app.models.user import User

_NICKNAME_RE = re.compile(r"^[0-9A-Za-z가-힣]{2,10}$")


class UsersService:
    async def get_me(self, *, user_id: int) -> User:
        user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
        if not user:
            raise ValueError("User not found")
        return user

    async def update_nickname(self, *, user_id: int, nickname: str) -> User:
        if not _NICKNAME_RE.match(nickname):
            raise ValueError("Invalid nickname")

        user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
        if not user:
            raise ValueError("User not found")

        user.nickname = nickname
        try:
            await user.save(update_fields=["nickname", "updated_at"])
        except IntegrityError as e:
            raise ValueError("Nickname already exists") from e
        return user
