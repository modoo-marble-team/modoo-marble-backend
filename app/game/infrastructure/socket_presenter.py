from __future__ import annotations

from app.game.models import GameState, PendingPrompt
from app.game.presentation import serialize_game_patch
from app.game.rules import serialize_prompt


class GameSocketPresenter:
    def serialize_prompt(self, prompt: PendingPrompt | None) -> dict | None:
        return serialize_prompt(prompt)

    def serialize_patch_packet(
        self,
        *,
        state: GameState,
        events: list[dict],
        patches: list[dict] | None = None,
        include_snapshot: bool = False,
    ) -> dict:
        return serialize_game_patch(
            state,
            events=events,
            patches=patches,
            include_snapshot=include_snapshot,
        )
