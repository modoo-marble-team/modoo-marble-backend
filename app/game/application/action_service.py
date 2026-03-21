"""게임 액션 실행 흐름을 감싸는 애플리케이션 계층.

잠금, 상태 로드/저장, 규칙 실행 순서를 여기서 관리한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.game.actions.dispatch import dispatch_game_action
from app.game.infrastructure.state_repository import GameStateRepository
from app.game.models import GameState
from app.game.rules import process_prompt_response
from app.game.state import apply_patches


class GameNotFoundError(RuntimeError):
    pass


class GameMembershipError(RuntimeError):
    pass


class GameDesyncError(RuntimeError):
    pass


@dataclass(slots=True)
class GameCommandResult:
    # 액션 실행 뒤 소켓 계층에 넘길 결과 묶음.
    state: GameState
    events: list[dict]
    patches: list[dict]
    previous_turn: int
    previous_player_id: int


class GameActionService:
    def __init__(self, repository: GameStateRepository | None = None) -> None:
        self._repository = repository or GameStateRepository()

    async def execute_action(
        self,
        *,
        game_id: str,
        user_id: int,
        action_type: str,
        data: dict,
        known_revision: int | None = None,
    ) -> GameCommandResult:
        def mutate(state: GameState) -> tuple[list[dict], list[dict]]:
            return dispatch_game_action(
                state,
                user_id=user_id,
                action_type=action_type,
                data=data,
            )

        return await self._mutate_game(
            game_id=game_id,
            user_id=user_id,
            known_revision=known_revision,
            mutate=mutate,
        )

    async def respond_prompt(
        self,
        *,
        game_id: str,
        user_id: int,
        prompt_id: str,
        choice: str,
        payload: dict | None,
        known_revision: int | None = None,
    ) -> GameCommandResult:
        def mutate(state: GameState) -> tuple[list[dict], list[dict]]:
            return process_prompt_response(
                state,
                player_id=user_id,
                prompt_id=prompt_id,
                choice=choice,
                payload=payload,
            )

        return await self._mutate_game(
            game_id=game_id,
            user_id=user_id,
            known_revision=known_revision,
            mutate=mutate,
        )

    async def _mutate_game(
        self,
        *,
        game_id: str,
        user_id: int,
        known_revision: int | None,
        mutate: Callable[[GameState], tuple[list[dict], list[dict]]],
    ) -> GameCommandResult:
        # 모든 액션은
        # 잠금 획득 -> 상태 로드 -> 규칙 실행 -> 패치 적용 -> 저장 순서로 처리한다.
        async with self._repository.lock(game_id):
            state = await self._repository.load(game_id)
            if state is None:
                raise GameNotFoundError(game_id)

            if user_id not in state.players:
                raise GameMembershipError(user_id)

            if known_revision is not None and state.revision != known_revision:
                raise GameDesyncError(
                    f"expected revision {known_revision}, actual {state.revision}"
                )

            previous_turn = state.turn
            previous_player_id = state.current_player_id
            events, patches = mutate(state)
            apply_patches(state, patches)
            state.revision += 1
            await self._repository.save(game_id, state)

            return GameCommandResult(
                state=state,
                events=events,
                patches=patches,
                previous_turn=previous_turn,
                previous_player_id=previous_player_id,
            )
