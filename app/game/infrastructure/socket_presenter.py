"""도메인 상태를 소켓 전송용 payload로 바꾸는 어댑터."""

from __future__ import annotations

from app.game.models import GameState, PendingPrompt
from app.game.presentation import serialize_game_patch
from app.game.rules import serialize_prompt


class GameSocketPresenter:
    def serialize_prompt(self, prompt: PendingPrompt | None) -> dict | None:
        # 프롬프트 dataclass를 소켓 payload 형태로 바꾼다.
        return serialize_prompt(prompt)

    def serialize_patch_packet(
        self,
        *,
        state: GameState,
        events: list[dict],
        patches: list[dict] | None = None,
        include_snapshot: bool = False,
    ) -> dict:
        # 이벤트/패치 묶음을 프론트가 받는 패킷으로 만든다.
        return serialize_game_patch(
            state,
            events=events,
            patches=patches,
            include_snapshot=include_snapshot,
        )
