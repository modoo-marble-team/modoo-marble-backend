from __future__ import annotations

import structlog

from app.game.models import GameState
from app.models.game import Game
from app.models.user_game import UserGame

logger = structlog.get_logger()


def _compute_placements(state: GameState) -> dict[int, int]:
    sorted_players = sorted(
        state.players.values(),
        key=lambda p: (-p.balance, p.turn_order),
    )
    return {p.player_id: i + 1 for i, p in enumerate(sorted_players)}


async def persist_game_result(state: GameState) -> None:
    try:
        game_id = int(state.game_id)
        placements = _compute_placements(state)

        user_games = await UserGame.filter(game_id=game_id).select_related("user")

        guest_ids: set[int] = set()
        for ug in user_games:
            if ug.user.is_guest:
                guest_ids.add(ug.user_id)
                continue
            rank = placements.get(ug.user_id)
            if rank is not None:
                ug.placement = rank
                await ug.save(update_fields=["placement"])

        game = await Game.get(id=game_id)
        winner_id = state.winner_id
        if winner_id is not None and winner_id not in guest_ids:
            game.winner_id = winner_id
        game.round_count = state.round
        await game.save(update_fields=["winner_id", "round_count"])

    except Exception:
        logger.exception("persist_game_result failed", game_id=state.game_id)
