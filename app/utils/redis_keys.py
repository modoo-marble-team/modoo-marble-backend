from __future__ import annotations


class RedisKeys:
    """중앙화된 Redis 키 생성 유틸리티.

    모든 Redis 키를 한 곳에서 관리하여 오타 및 불일치를 방지합니다.
    """

    @staticmethod
    def room(room_id: str) -> str:
        """방 데이터 키."""
        return f"room:{room_id}"

    @staticmethod
    def user_room(user_id: int) -> str:
        """유저가 현재 참가한 방 ID 키."""
        return f"user:{user_id}:room"

    @staticmethod
    def user_game(user_id: int) -> str:
        """유저가 현재 참여 중인 게임 ID 키."""
        return f"user:{user_id}:game"

    @staticmethod
    def user_active_game(user_id: int) -> str:
        """유저의 활성 게임 ID 키 (sync runtime 사용)."""
        return f"game:user:{user_id}:active"

    @staticmethod
    def rooms_index() -> str:
        """전체 방 ID 집합 키."""
        return "rooms:index"
