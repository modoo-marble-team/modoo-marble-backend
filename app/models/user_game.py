from tortoise import fields, models


class UserGame(models.Model):
    id = fields.IntField(pk=True)

    # "models.User", "models.Game" 문자열 참조를 통해 결합도를 낮춥니다.
    user = fields.ForeignKeyField(
        "models.User", related_name="game_records", index=True
    )
    game = fields.ForeignKeyField(
        "models.Game", related_name="participants", index=True
    )

    tolls_paid = fields.BigIntField(default=0)
    tiles_purchased = fields.SmallIntField(default=0)
    buildings_built = fields.SmallIntField(default=0)
    placement = fields.SmallIntField(null=True)

    class Meta:
        table = "user_games"
        unique_together = (("user", "game"),)
