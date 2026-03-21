from tortoise import fields, models


class Game(models.Model):
    id = fields.IntField(primary_key=True)

    winner = fields.ForeignKeyField(
        "models.User", related_name="won_games", null=True, on_delete=fields.SET_NULL
    )
    round_count = fields.SmallIntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "games"
