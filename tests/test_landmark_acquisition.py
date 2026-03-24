"""랜드마크 인수 제한 관련 테스트.

랜드마크(최고 건물 단계)에 도달한 땅은
- 직접 인수(apply_property_acquisition)
- 찬스카드 STEAL_PROPERTY
- 찬스카드 GIVE_PROPERTY
모두 대상에서 제외되어야 한다.
"""

from __future__ import annotations

import pytest

from app.game.board import BOARD
from app.game.domain.card_effects import (
    CardEffectContext,
    GivePropertyCardEffect,
    StealPropertyCardEffect,
)
from app.game.domain.property_actions import (
    PropertyActionContext,
    apply_property_acquisition,
)
from app.game.enums import PlayerState, TileType
from app.game.errors import GameActionError
from app.game.game_rules import MAX_BUILDING_LEVEL
from app.game.models import GameState, GlobalEffectState, PlayerGameState, TileGameState
from app.game.state import INITIAL_BALANCE

# 테스트에 사용할 PROPERTY 타입 타일 ID 두 개를 보드에서 추출
_PROPERTY_TILE_IDS = [
    tile.tile_id for tile in BOARD if tile.tile_type == TileType.PROPERTY
]
TILE_A = _PROPERTY_TILE_IDS[0]
TILE_B = _PROPERTY_TILE_IDS[1]


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def make_state(
    *,
    player1_balance: int = INITIAL_BALANCE,
    player2_balance: int = INITIAL_BALANCE,
    tile_a_owner: int | None = None,
    tile_a_level: int = 0,
    tile_b_owner: int | None = None,
    tile_b_level: int = 0,
) -> GameState:
    """기본 2인 게임 상태를 만든다."""
    player1_owned = []
    player1_levels: dict[int, int] = {}
    player2_owned = []
    player2_levels: dict[int, int] = {}

    if tile_a_owner == 1:
        player1_owned.append(TILE_A)
        player1_levels[TILE_A] = tile_a_level
    elif tile_a_owner == 2:
        player2_owned.append(TILE_A)
        player2_levels[TILE_A] = tile_a_level

    if tile_b_owner == 1:
        player1_owned.append(TILE_B)
        player1_levels[TILE_B] = tile_b_level
    elif tile_b_owner == 2:
        player2_owned.append(TILE_B)
        player2_levels[TILE_B] = tile_b_level

    tiles = {
        tile.tile_id: TileGameState(owner_id=None, building_level=0)
        for tile in BOARD
        if tile.tile_type == TileType.PROPERTY
    }
    tiles[TILE_A] = TileGameState(owner_id=tile_a_owner, building_level=tile_a_level)
    tiles[TILE_B] = TileGameState(owner_id=tile_b_owner, building_level=tile_b_level)

    return GameState(
        game_id="test",
        room_id="room-test",
        revision=0,
        turn=1,
        round=1,
        current_player_id=1,
        status="playing",
        phase="WAIT_ROLL",
        pending_prompt=None,
        winner_id=None,
        global_effects=GlobalEffectState(),
        players={
            1: PlayerGameState(
                player_id=1,
                nickname="player1",
                balance=player1_balance,
                current_tile_id=0,
                player_state=PlayerState.NORMAL,
                state_duration=0,
                consecutive_doubles=0,
                owned_tiles=player1_owned,
                building_levels=player1_levels,
                turn_order=0,
            ),
            2: PlayerGameState(
                player_id=2,
                nickname="player2",
                balance=player2_balance,
                current_tile_id=0,
                player_state=PlayerState.NORMAL,
                state_duration=0,
                consecutive_doubles=0,
                owned_tiles=player2_owned,
                building_levels=player2_levels,
                turn_order=1,
            ),
        },
        tiles=tiles,
    )


def make_property_action_context(
    *, max_building_level: int = MAX_BUILDING_LEVEL
) -> PropertyActionContext:
    return PropertyActionContext(
        max_building_level=max_building_level,
        get_sell_refund=lambda tile_id, level: 0,
        get_purchase_cost=lambda state, tile_id: 10_000,
        get_build_cost=lambda state, tile_id, level: 10_000,
        get_acquisition_cost=lambda state, tile_id, level: 50_000,
        get_toll_amount=lambda state, tile_id, level: 5_000,
        owned_tile_patches=lambda state, player_id, tile_id: [],
        bankrupt_player_patches=lambda state, player_id: [],
        bankrupt_player_events=lambda player_id: [],
        append_game_over_if_last_survivor=lambda state, patches, events: None,
    )


def make_card_effect_context(
    *, max_building_level: int = MAX_BUILDING_LEVEL
) -> CardEffectContext:
    return CardEffectContext(
        board_size=32,
        start_salary=20_000,
        max_building_level=max_building_level,
        apply_money_delta=lambda state, player_id, amount: ([], []),
        choose_random=lambda items: items[0],  # 항상 첫 번째 항목 선택 (결정론적)
        player_name=lambda state, player_id: f"player{player_id}",
        tile_name=lambda tile_id: f"tile{tile_id}",
        get_object_particle=lambda name: "을",
    )


# ---------------------------------------------------------------------------
# apply_property_acquisition 테스트
# ---------------------------------------------------------------------------


class TestApplyPropertyAcquisitionLandmarkRestriction:
    def test_landmark_cannot_be_acquired(self):
        """랜드마크(최고 건물 단계) 땅은 인수할 수 없어야 한다."""
        state = make_state(tile_a_owner=2, tile_a_level=MAX_BUILDING_LEVEL)
        context = make_property_action_context()

        with pytest.raises(GameActionError) as exc_info:
            apply_property_acquisition(
                state,
                player_id=1,
                tile_id=TILE_A,
                context=context,
            )

        assert exc_info.value.code == "LANDMARK_NOT_ACQUIRABLE"

    def test_non_landmark_can_be_acquired(self):
        """랜드마크가 아닌 땅은 정상적으로 인수할 수 있어야 한다."""
        state = make_state(tile_a_owner=2, tile_a_level=MAX_BUILDING_LEVEL - 1)
        context = make_property_action_context()

        events, patches = apply_property_acquisition(
            state,
            player_id=1,
            tile_id=TILE_A,
            context=context,
        )

        assert len(events) > 0
        assert len(patches) > 0

    def test_unowned_tile_cannot_be_acquired(self):
        """소유자가 없는 땅은 인수 대상이 아니다."""
        state = make_state(tile_a_owner=None, tile_a_level=0)
        context = make_property_action_context()

        with pytest.raises(GameActionError) as exc_info:
            apply_property_acquisition(
                state,
                player_id=1,
                tile_id=TILE_A,
                context=context,
            )

        assert exc_info.value.code == "INVALID_PHASE"


# ---------------------------------------------------------------------------
# StealPropertyCardEffect 테스트
# ---------------------------------------------------------------------------


class TestStealPropertyCardEffectLandmarkRestriction:
    _CARD = {
        "type": "STEAL_PROPERTY",
        "description": "$player$의 $property$$suffix$ 빼앗았습니다.",
        "failed_description": "빼앗을 땅이 없습니다.",
    }

    def test_landmark_excluded_from_steal_pool(self):
        """상대가 랜드마크만 소유한 경우 STEAL_PROPERTY는 실패(빈 풀) 처리되어야 한다."""
        state = make_state(tile_a_owner=2, tile_a_level=MAX_BUILDING_LEVEL)
        context = make_card_effect_context()
        effect = StealPropertyCardEffect(effect_type="STEAL_PROPERTY")

        events, patches = effect.apply(
            state=state,
            player_id=1,
            card=self._CARD,
            context=context,
        )

        # 패치가 없어야 한다 (땅이 이전되지 않음)
        assert patches == []
        # 이벤트는 실패 메시지로 반환된다
        assert len(events) == 1
        assert events[0]["type"] == "CHANCE_RESOLVED"

    def test_non_landmark_is_stolen(self):
        """상대가 랜드마크가 아닌 땅을 소유한 경우 정상적으로 빼앗아야 한다."""
        state = make_state(tile_a_owner=2, tile_a_level=MAX_BUILDING_LEVEL - 1)
        context = make_card_effect_context()
        effect = StealPropertyCardEffect(effect_type="STEAL_PROPERTY")

        events, patches = effect.apply(
            state=state,
            player_id=1,
            card=self._CARD,
            context=context,
        )

        assert len(patches) > 0
        # 소유권 이전 패치가 포함되어야 한다
        assert any(str(TILE_A) in str(p) for p in patches)

    def test_landmark_skipped_non_landmark_stolen(self):
        """상대가 랜드마크와 일반 땅을 함께 소유한 경우 일반 땅만 빼앗아야 한다."""
        state = make_state(
            tile_a_owner=2,
            tile_a_level=MAX_BUILDING_LEVEL,
            tile_b_owner=2,
            tile_b_level=0,
        )
        # choose_random이 항상 첫 번째 항목을 선택하므로
        # 랜드마크(TILE_A)가 풀에서 제외된 뒤 TILE_B가 선택되어야 한다
        context = make_card_effect_context()
        effect = StealPropertyCardEffect(effect_type="STEAL_PROPERTY")

        events, patches = effect.apply(
            state=state,
            player_id=1,
            card=self._CARD,
            context=context,
        )

        assert len(patches) > 0
        stolen_patch = next(
            (p for p in patches if "owner_id" in str(p) and str(TILE_B) in str(p)), None
        )
        assert stolen_patch is not None, "일반 땅(TILE_B)이 빼앗겨야 한다"


# ---------------------------------------------------------------------------
# GivePropertyCardEffect 테스트
# ---------------------------------------------------------------------------


class TestGivePropertyCardEffectLandmarkRestriction:
    _CARD = {
        "type": "GIVE_PROPERTY",
        "description": "$player$에게 $property$$suffix$ 넘겼습니다.",
        "failed_description": "넘길 땅이 없습니다.",
    }

    def test_landmark_excluded_from_give_pool(self):
        """랜드마크만 소유한 경우 GIVE_PROPERTY는 실패(빈 풀) 처리되어야 한다."""
        state = make_state(tile_a_owner=1, tile_a_level=MAX_BUILDING_LEVEL)
        context = make_card_effect_context()
        effect = GivePropertyCardEffect(effect_type="GIVE_PROPERTY")

        events, patches = effect.apply(
            state=state,
            player_id=1,
            card=self._CARD,
            context=context,
        )

        assert patches == []
        assert len(events) == 1
        assert events[0]["type"] == "CHANCE_RESOLVED"

    def test_non_landmark_is_given(self):
        """랜드마크가 아닌 땅을 소유한 경우 정상적으로 넘겨야 한다."""
        state = make_state(tile_a_owner=1, tile_a_level=MAX_BUILDING_LEVEL - 1)
        context = make_card_effect_context()
        effect = GivePropertyCardEffect(effect_type="GIVE_PROPERTY")

        events, patches = effect.apply(
            state=state,
            player_id=1,
            card=self._CARD,
            context=context,
        )

        assert len(patches) > 0

    def test_landmark_kept_non_landmark_given(self):
        """랜드마크와 일반 땅을 함께 소유한 경우 일반 땅만 넘겨야 한다."""
        state = make_state(
            tile_a_owner=1,
            tile_a_level=MAX_BUILDING_LEVEL,
            tile_b_owner=1,
            tile_b_level=0,
        )
        context = make_card_effect_context()
        effect = GivePropertyCardEffect(effect_type="GIVE_PROPERTY")

        events, patches = effect.apply(
            state=state,
            player_id=1,
            card=self._CARD,
            context=context,
        )

        assert len(patches) > 0
        given_patch = next(
            (p for p in patches if "owner_id" in str(p) and str(TILE_B) in str(p)), None
        )
        assert given_patch is not None, "일반 땅(TILE_B)이 넘겨져야 한다"
