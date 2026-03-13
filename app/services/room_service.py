from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.errors import ApiError
from app.game.state import init_game_state
from app.models.game import Game
from app.models.user import User
from app.models.user_game import UserGame
from app.redis_client import get_redis

ROOMS_INDEX_KEY = "rooms:index"
ROOM_TTL_SECONDS = 60 * 60 * 24
MAX_CHAT_MESSAGES = 100


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _room_key(room_id: str) -> str:
    return f"room:{room_id}"


def _user_room_key(user_id: int) -> str:
    return f"user:{user_id}:room"


class RoomService:
    async def _get_user(self, user_id: int) -> User:
        user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
        if not user:
            raise ApiError(
                status_code=404,
                code="USER_NOT_FOUND",
                message="사용자를 찾을 수 없습니다.",
            )
        return user

    async def _save_room(self, room: dict) -> None:
        redis = get_redis()
        room["updated_at"] = _now_iso()
        await redis.set(_room_key(room["id"]), json.dumps(room), ex=ROOM_TTL_SECONDS)
        await redis.sadd(ROOMS_INDEX_KEY, room["id"])

    async def _delete_room(self, room_id: str) -> None:
        redis = get_redis()
        await redis.delete(_room_key(room_id))
        await redis.srem(ROOMS_INDEX_KEY, room_id)

    async def get_room(self, room_id: str) -> dict | None:
        redis = get_redis()
        raw = await redis.get(_room_key(room_id))
        if raw is None:
            return None
        return json.loads(raw)

    async def _require_room(self, room_id: str) -> dict:
        room = await self.get_room(room_id)
        if room is None:
            raise ApiError(
                status_code=404,
                code="ROOM_NOT_FOUND",
                message="방을 찾을 수 없습니다.",
            )
        return room

    async def _get_user_room_id(self, user_id: int) -> str | None:
        redis = get_redis()
        return await redis.get(_user_room_key(user_id))

    async def _set_user_room_id(self, user_id: int, room_id: str) -> None:
        redis = get_redis()
        await redis.set(_user_room_key(user_id), room_id, ex=ROOM_TTL_SECONDS)

    async def _clear_user_room_id(self, user_id: int) -> None:
        redis = get_redis()
        await redis.delete(_user_room_key(user_id))

    def _host_player(self, room: dict) -> dict:
        for player in room["players"]:
            if player["is_host"]:
                return player
        return room["players"][0]

    def _room_card(self, room: dict) -> dict:
        host = self._host_player(room)
        return {
            "id": room["id"],
            "title": room["title"],
            "status": room["status"],
            "current_players": len(room["players"]),
            "max_players": room["max_players"],
            "is_private": room["is_private"],
            "host_id": host["id"],
            "host_nickname": host["nickname"],
        }

    def _room_snapshot(self, room: dict) -> dict:
        return {
            "room_id": room["id"],
            "title": room["title"],
            "status": room["status"],
            "max_players": room["max_players"],
            "is_private": room["is_private"],
            "players": room["players"],
            "chat_messages": room["chat_messages"],
        }

    def _require_member(self, room: dict, user_id: int) -> dict:
        for player in room["players"]:
            if player["id"] == str(user_id):
                return player
        raise ApiError(
            status_code=403,
            code="NOT_ROOM_MEMBER",
            message="방 멤버가 아닙니다.",
        )

    def _all_ready(self, room: dict) -> bool:
        if len(room["players"]) < 2:
            return False
        return all(
            player["is_host"] or player["is_ready"] for player in room["players"]
        )

    async def list_rooms(
        self,
        *,
        status: str | None,
        exclude_private: bool,
        keyword: str | None,
    ) -> list[dict]:
        redis = get_redis()
        room_ids = sorted(await redis.smembers(ROOMS_INDEX_KEY))
        rooms: list[dict] = []

        for room_id in room_ids:
            room = await self.get_room(room_id)
            if room is None:
                await redis.srem(ROOMS_INDEX_KEY, room_id)
                continue
            if status and room["status"] != status:
                continue
            if exclude_private and room["is_private"]:
                continue
            if keyword and keyword.lower() not in room["title"].lower():
                continue
            rooms.append(self._room_card(room))

        return rooms

    async def create_room(
        self,
        *,
        user_id: int,
        title: str,
        is_private: bool,
        password: str | None,
        max_players: int,
    ) -> dict:
        user = await self._get_user(user_id)
        existing_room_id = await self._get_user_room_id(user_id)
        if existing_room_id:
            raise ApiError(
                status_code=409,
                code="ALREADY_JOINED_ROOM",
                message="이미 다른 방에 참가 중입니다.",
            )

        if is_private and not password:
            raise ApiError(
                status_code=400,
                code="ROOM_PASSWORD_REQUIRED",
                message="비공개 방 비밀번호가 필요합니다.",
            )

        room_id = f"room-{uuid4().hex[:8]}"
        now = _now_iso()
        room = {
            "id": room_id,
            "title": title,
            "status": "waiting",
            "is_private": is_private,
            "password": password,
            "max_players": max_players,
            "host_user_id": str(user.id),
            "game_id": None,
            "players": [
                {
                    "id": str(user.id),
                    "nickname": user.nickname,
                    "is_ready": False,
                    "is_host": True,
                }
            ],
            "chat_messages": [],
            "created_at": now,
            "updated_at": now,
        }
        await self._save_room(room)
        await self._set_user_room_id(user_id, room_id)
        return room

    async def join_room(
        self,
        *,
        room_id: str,
        user_id: int,
        password: str | None,
    ) -> dict:
        room = await self._require_room(room_id)
        user = await self._get_user(user_id)
        existing_room_id = await self._get_user_room_id(user_id)

        if existing_room_id and existing_room_id != room_id:
            raise ApiError(
                status_code=409,
                code="ALREADY_JOINED_ROOM",
                message="이미 다른 방에 참가 중입니다.",
            )

        for player in room["players"]:
            if player["id"] == str(user_id):
                await self._set_user_room_id(user_id, room_id)
                return room

        if room["status"] != "waiting":
            raise ApiError(
                status_code=409,
                code="ROOM_NOT_READY_TO_START",
                message="이미 시작된 방입니다.",
            )

        if room["is_private"]:
            if not password:
                raise ApiError(
                    status_code=400,
                    code="ROOM_PASSWORD_REQUIRED",
                    message="비공개 방 비밀번호가 필요합니다.",
                )
            if room["password"] != password:
                raise ApiError(
                    status_code=403,
                    code="ROOM_PASSWORD_MISMATCH",
                    message="비밀번호가 일치하지 않습니다.",
                )

        if len(room["players"]) >= room["max_players"]:
            raise ApiError(
                status_code=409,
                code="ROOM_FULL",
                message="방이 가득 찼습니다.",
            )

        room["players"].append(
            {
                "id": str(user.id),
                "nickname": user.nickname,
                "is_ready": False,
                "is_host": False,
            }
        )
        await self._save_room(room)
        await self._set_user_room_id(user_id, room_id)
        return room

    async def leave_room(
        self,
        *,
        room_id: str,
        user_id: int,
    ) -> tuple[dict | None, str | None]:
        room = await self._require_room(room_id)
        player = self._require_member(room, user_id)

        room["players"] = [
            existing for existing in room["players"] if existing["id"] != player["id"]
        ]
        await self._clear_user_room_id(user_id)

        new_host_id: str | None = None
        if not room["players"]:
            await self._delete_room(room_id)
            return None, None

        if player["is_host"]:
            room["players"][0]["is_host"] = True
            room["players"][0]["is_ready"] = False
            room["host_user_id"] = room["players"][0]["id"]
            new_host_id = room["players"][0]["id"]

        await self._save_room(room)
        return room, new_host_id

    async def toggle_ready(self, *, room_id: str, user_id: int) -> tuple[dict, bool]:
        room = await self._require_room(room_id)
        player = self._require_member(room, user_id)

        if player["is_host"]:
            return room, False

        player["is_ready"] = not player["is_ready"]
        await self._save_room(room)
        return room, bool(player["is_ready"])

    async def add_chat_message(
        self,
        *,
        room_id: str,
        user_id: int,
        message: str,
    ) -> tuple[dict, dict]:
        room = await self._require_room(room_id)
        player = self._require_member(room, user_id)
        chat_message = {
            "id": f"chat-{uuid4().hex[:10]}",
            "sender_id": player["id"],
            "sender_nickname": player["nickname"],
            "message": message.strip(),
            "sent_at": _now_iso(),
            "type": "talk",
        }
        room["chat_messages"].append(chat_message)
        room["chat_messages"] = room["chat_messages"][-MAX_CHAT_MESSAGES:]
        await self._save_room(room)
        return room, chat_message

    async def start_game(self, *, room_id: str, user_id: int) -> tuple[dict, dict]:
        room = await self._require_room(room_id)
        host_player = self._host_player(room)
        if host_player["id"] != str(user_id):
            raise ApiError(
                status_code=403,
                code="ONLY_HOST_CAN_START",
                message="방장만 게임을 시작할 수 있습니다.",
            )
        if room["status"] != "waiting" or not self._all_ready(room):
            raise ApiError(
                status_code=409,
                code="ROOM_NOT_READY_TO_START",
                message="게임 시작 조건을 만족하지 않습니다.",
            )

        game = await Game.create(round_count=0)
        player_ids = [int(player["id"]) for player in room["players"]]
        nicknames = {
            int(player["id"]): player["nickname"] for player in room["players"]
        }

        for player_id in player_ids:
            await UserGame.create(game_id=int(game.id), user_id=player_id)

        game_state = await init_game_state(
            game_id=str(game.id),
            room_id=room["id"],
            player_ids=player_ids,
            nicknames=nicknames,
        )

        # 유저-게임 매핑 Redis에 저장 (재연결 시 사용)
        redis = get_redis()
        for player_id in player_ids:
            await redis.set(
                f"user:{player_id}:game",
                str(game.id),
                ex=ROOM_TTL_SECONDS,
            )

        room["status"] = "playing"
        room["game_id"] = str(game.id)
        await self._save_room(room)
        return room, game_state

    def room_card(self, room: dict) -> dict:
        return self._room_card(room)

    def room_snapshot(self, room: dict) -> dict:
        return self._room_snapshot(room)

    def all_ready(self, room: dict) -> bool:
        return self._all_ready(room)
