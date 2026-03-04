from __future__ import annotations

from tortoise import fields
from tortoise.fields import ReverseRelation
from tortoise.models import Model


class Game(Model):
    id = fields.IntField(pk=True)
    winner = fields.ForeignKeyField(
        "models.User",
        related_name="won_games",
        null=True,
        on_delete=fields.SET_NULL,
    )
    round_count = fields.SmallIntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)

    players: ReverseRelation[UserGame]

    class Meta:
        table = "games"
        indexes = (("winner",),)


class UserGame(Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField(
        "models.User",
        related_name="game_records",
    )
    game = fields.ForeignKeyField(
        "models.Game",
        related_name="players",
        on_delete=fields.CASCADE,
    )
    tolls_paid = fields.BigIntField(default=0)
    tiles_purchased = fields.SmallIntField(default=0)
    buildings_built = fields.SmallIntField(default=0)
    placement = fields.SmallIntField()

    class Meta:
        table = "user_games"
        unique_together = (("user", "game"),)
        indexes = (("game",), ("user",))
