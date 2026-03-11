from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.config import settings
from app.utils.jwt import decode_token


@dataclass(frozen=True)
class AuthUser:
    user_id: int
    is_guest: bool


async def get_auth_user(authorization: str | None = Header(default=None)) -> AuthUser:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    token = parts[1]
    try:
        payload = decode_token(
            secret=settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM, token=token
        )
    except ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="Token expired") from e
    except InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    try:
        user_id = int(sub)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token payload") from e

    is_guest = bool(payload.get("is_guest", False))
    return AuthUser(user_id=user_id, is_guest=is_guest)
