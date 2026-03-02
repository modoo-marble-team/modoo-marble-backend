"""
auth.py — 인증 서비스
JWT 발급/검증, 카카오 OAuth 처리
"""

from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from app.config import settings


def create_access_token(user_id: str, is_guest: bool = False) -> str:
    """JWT 액세스 토큰 생성."""
    expire = datetime.now(UTC) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "is_guest": is_guest,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """JWT 토큰 검증. 유효하면 payload 반환, 아니면 None."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None
