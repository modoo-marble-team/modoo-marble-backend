from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class Game(Model):
    id = fields.IntField(pk=True)
    winner = fields.ForeignKeyField(
        "models.User",
        related_name="games_won",
        null=True,
        on_delete=fields.SET_NULL,
        source_field="winner_id",
    )
    round_count = fields.SmallIntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "games"


class UserGame(Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField(
        "models.User",
        related_name="user_games",
        on_delete=fields.CASCADE,
        source_field="user_id",
    )
    game = fields.ForeignKeyField(
        "models.Game",
        related_name="user_games",
        on_delete=fields.CASCADE,
        source_field="game_id",
    )
    tolls_paid = fields.BigIntField()
    tiles_purchased = fields.SmallIntField()
    buildings_built = fields.SmallIntField()
    placement = fields.SmallIntField()

    class Meta:
        table = "user_games"
