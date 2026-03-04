from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class User(Model):
    id = fields.IntField(pk=True)
    kakao_id = fields.CharField(max_length=50, unique=True, null=True)
    nickname = fields.CharField(max_length=20, unique=True)
    profile_image_url = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "users"
