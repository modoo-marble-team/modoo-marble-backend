"""게임 결과 저장 서비스 단위 테스트."""

from __future__ import annotations

from app.game.board import BOARD
from app.game.enums import PlayerState, TileType
from app.game.models import GameState, PlayerGameState, TileGameState
from app.game.state import INITIAL_BALANCE
from app.services.game_result_service import _compute_placements

# 테스트에 쓸 PROPERTY 타일 2개 (tile_id=1, price=30000)
_PROPERTY_TILES = [t for t in BOARD if t.tile_type == TileType.PROPERTY]
TILE_A = _PROPERTY_TILES[0]
TILE_B = _PROPERTY_TILES[1]


def make_player(
    player_id: int,
    *,
    balance: int,
    turn_order: int,
    owned_tiles: list[int] | None = None,
    building_levels: dict[int, int] | None = None,
    player_state: PlayerState = PlayerState.NORMAL,
) -> PlayerGameState:
    return PlayerGameState(
        player_id=player_id,
        nickname=f"player{player_id}",
        balance=balance,
        current_tile_id=0,
        player_state=player_state,
        state_duration=0,
        consecutive_doubles=0,
        owned_tiles=owned_tiles or [],
        building_levels=building_levels or {},
        turn_order=turn_order,
    )


def make_state(
    players: dict[int, PlayerGameState], tiles: dict[int, TileGameState]
) -> GameState:
    return GameState(
        game_id="1",
        room_id="room-1",
        revision=0,
        turn=1,
        round=1,
        current_player_id=1,
        status="finished",
        phase="GAME_OVER",
        pending_prompt=None,
        winner_id=None,
        players=players,
        tiles=tiles,
    )


class TestComputePlacements:
    def test_ranks_by_total_assets_not_balance_only(self):
        """현금이 적어도 땅 가치를 포함한 총자산이 더 높으면 1위여야 한다.

        버그: balance만 비교하면 player2(잔액 높음)가 1위가 되지만
        올바른 로직은 총자산(잔액+부동산)으로 비교해야 한다.
        """
        tile_price = TILE_A.price  # 30000

        # player1: 잔액 0 + 땅(30000) = 총자산 30000
        # player2: 잔액 25000 + 땅 없음 = 총자산 25000
        # → 잔액만 보면 player2가 높지만, 총자산은 player1이 높다
        player1 = make_player(1, balance=0, turn_order=0, owned_tiles=[TILE_A.tile_id])
        player2 = make_player(2, balance=tile_price - 5000, turn_order=1)

        tiles = {TILE_A.tile_id: TileGameState(owner_id=1, building_level=0)}
        state = make_state({1: player1, 2: player2}, tiles)

        placements = _compute_placements(state)

        assert placements[1] == 1, "총자산이 더 높은 player1이 1위여야 한다"
        assert placements[2] == 2

    def test_building_level_increases_asset_value(self):
        """건물 레벨이 높을수록 자산 가치가 높아져 순위에 반영돼야 한다."""
        # player1: 잔액 0 + 건물 1단계 땅
        # player2: 잔액 0 + 건물 없는 같은 땅
        # → 건물 비용만큼 player1 총자산이 더 높아야 한다
        build_cost = TILE_A.build_costs[0]

        player1 = make_player(1, balance=0, turn_order=0, owned_tiles=[TILE_A.tile_id])
        player2 = make_player(
            2, balance=build_cost - 1, turn_order=1, owned_tiles=[TILE_B.tile_id]
        )

        tiles = {
            TILE_A.tile_id: TileGameState(owner_id=1, building_level=1),
            TILE_B.tile_id: TileGameState(owner_id=2, building_level=0),
        }
        state = make_state({1: player1, 2: player2}, tiles)

        placements = _compute_placements(state)

        assert placements[1] == 1
        assert placements[2] == 2

    def test_tie_broken_by_turn_order(self):
        """총자산이 같으면 턴 순서(turn_order)가 빠른 플레이어가 높은 순위여야 한다."""
        player1 = make_player(1, balance=INITIAL_BALANCE, turn_order=1)
        player2 = make_player(2, balance=INITIAL_BALANCE, turn_order=0)

        state = make_state({1: player1, 2: player2}, {})

        placements = _compute_placements(state)

        # turn_order=0인 player2가 더 앞 순위
        assert placements[2] == 1
        assert placements[1] == 2

    def test_three_players_sorted_correctly(self):
        """3인 게임에서 총자산 기준으로 올바르게 1·2·3위가 결정돼야 한다."""
        tile_price = TILE_A.price  # 30000

        # player1: 잔액 10000 + 땅(30000) = 총자산 40000 → 1위
        # player2: 잔액 30000                = 총자산 30000 → 2위
        # player3: 잔액  5000                = 총자산  5000 → 3위
        player1 = make_player(
            1, balance=10000, turn_order=0, owned_tiles=[TILE_A.tile_id]
        )
        player2 = make_player(2, balance=tile_price, turn_order=1)
        player3 = make_player(3, balance=5000, turn_order=2)

        tiles = {TILE_A.tile_id: TileGameState(owner_id=1, building_level=0)}
        state = make_state({1: player1, 2: player2, 3: player3}, tiles)

        placements = _compute_placements(state)

        assert placements[1] == 1
        assert placements[2] == 2
        assert placements[3] == 3
