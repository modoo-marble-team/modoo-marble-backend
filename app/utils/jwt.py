from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError


def create_access_token(
    *, secret: str, algorithm: str, exp_minutes: int, user_id: int, is_guest: bool
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "is_guest": is_guest,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=exp_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def create_refresh_token(
    *, secret: str, algorithm: str, exp_days: int, user_id: int, jti: str
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=exp_days)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(*, secret: str, algorithm: str, token: str) -> dict[str, Any]:
    decoded = jwt.decode(token, secret, algorithms=[algorithm])
    if not isinstance(decoded, dict):
        raise InvalidTokenError("Invalid token payload")
    return decoded
