from __future__ import annotations

import re
from datetime import UTC, datetime

from tortoise.exceptions import IntegrityError

from app.models.user import User
from app.utils.exceptions import (
    InvalidNicknameError,
    NicknameAlreadyExistsError,
    UserNotFoundError,
)

_NICKNAME_RE = re.compile(r"^[0-9A-Za-z가-힣]{2,10}$")


class UsersService:
    async def _get_active_user_or_raise(self, user_id: int) -> User:
        user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
        if not user:
            raise UserNotFoundError()
        return user

    def _validate_nickname_or_raise(self, nickname: str) -> str:
        if not _NICKNAME_RE.fullmatch(nickname):
            raise InvalidNicknameError()
        return nickname

    async def get_me(self, *, user_id: int) -> User:
        return await self._get_active_user_or_raise(user_id)

    async def update_nickname(self, *, user_id: int, nickname: str) -> str:
        validated_nickname = self._validate_nickname_or_raise(nickname)
        now = datetime.now(UTC)

        try:
            updated_count = await User.filter(
                id=user_id,
                deleted_at__isnull=True,
            ).update(
                nickname=validated_nickname,
                updated_at=now,
            )
        except IntegrityError as e:
            raise NicknameAlreadyExistsError() from e

        if updated_count == 0:
            raise UserNotFoundError()

        return validated_nickname
