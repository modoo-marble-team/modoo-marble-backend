"""
models.py — Tortoise ORM 모델 정의
테이블 명세서 v4 기준.
Aerich 마이그레이션: aerich init -t app.config.TORTOISE_ORM → aerich migrate → aerich upgrade
"""

import uuid

from tortoise import fields
from tortoise.models import Model


class User(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    kakao_id = fields.CharField(max_length=50, unique=True, null=True)
    nickname = fields.CharField(max_length=20, unique=True)
    profile_image = fields.TextField(null=True)
    is_guest = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "users"

    @classmethod
    async def get_or_none_by_kakao(cls, kakao_id: str):
        return await cls.get_or_none(kakao_id=kakao_id)


class Room(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    title = fields.CharField(max_length=50)
    host = fields.ForeignKeyField("models.User", related_name="hosted_rooms")
    is_private = fields.BooleanField(default=False)
    password_hash = fields.CharField(max_length=255, null=True)
    status = fields.CharField(max_length=20, default="waiting")
    max_players = fields.SmallIntField(default=4)
    current_players = fields.SmallIntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "rooms"


class RoomPlayer(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    room = fields.ForeignKeyField(
        "models.Room", related_name="players", on_delete=fields.CASCADE
    )
    user = fields.ForeignKeyField("models.User", related_name="room_memberships")
    is_ready = fields.BooleanField(default=False)
    joined_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "room_players"
        unique_together = (("room", "user"),)


class Game(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    room = fields.ForeignKeyField("models.Room", related_name="games")
    round_count = fields.SmallIntField(default=0)
    end_reason = fields.CharField(max_length=30, null=True)
    winner = fields.ForeignKeyField(
        "models.User",
        related_name="won_games",
        null=True,
        on_delete=fields.SET_NULL,
    )
    started_at = fields.DatetimeField(auto_now_add=True)
    ended_at = fields.DatetimeField(null=True)

    class Meta:
        table = "games"


class GamePlayer(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    game = fields.ForeignKeyField(
        "models.Game", related_name="players", on_delete=fields.CASCADE
    )
    user = fields.ForeignKeyField("models.User", related_name="game_records")
    final_rank = fields.SmallIntField(null=True)
    final_assets = fields.BigIntField(default=0)
    is_winner = fields.BooleanField(default=False)

    class Meta:
        table = "game_players"
