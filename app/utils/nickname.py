from __future__ import annotations

import random
import string

from app.models.user import User


def make_guest_nickname() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"Guest_{suffix}"


async def make_unique_nickname(base: str) -> str:
    nick = base[:20]
    exists = await User.filter(nickname=nick, deleted_at__isnull=True).exists()
    if not exists:
        return nick

    for i in range(1, 1000):
        candidate = f"{nick[:16]}_{i}"
        exists2 = await User.filter(
            nickname=candidate, deleted_at__isnull=True
        ).exists()
        if not exists2:
            return candidate

    return f"{nick[:12]}_{random.randint(1000, 9999)}"
