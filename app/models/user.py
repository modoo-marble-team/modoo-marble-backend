import uuid  # noqa: F401

from tortoise import Model, fields

# from tortoise import Model == from tortoise.models import Model
# tortoise가 편의상 두 경로 모두 지원해요.


class User(Model):
    id = fields.IntField(pk=True)
    kakao_id = fields.CharField(max_length=50, null=True, unique=True)
    nickname = fields.CharField(max_length=20, unique=True)
    hashed_password = fields.CharField(max_length=128, null=True)
    profile_image_url = fields.TextField(null=True)
    is_guest = fields.BooleanField(default=False)

    deleted_at = fields.DatetimeField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)  # 생성될 때 딱 한 번
    updated_at = fields.DatetimeField(auto_now=True)  # 수정될 때마다 갱신

    # Tortoise ORM에게 "이 모델은 DB에서 users라는 테이블 이름을 써"라고 알려주는 것
    class Meta:
        table = "users"

    # Tortoise는 라이브러리 (=공구함)
    # FastAPI는 프레임워크 (=공장 컨베이어 벨트)
    # 구분 기준은 주도권
    # 라이브러리 -> 내가 주도권을 가짐. 필요할 때 내가 꺼내서 씀.
    # 프레임워크 -> 프레임워크가 주도권을 가짐. 정해진 구조에 내가 맞춤.

    def __str__(self):
        return f"[{self.id}] {self.nickname}"
