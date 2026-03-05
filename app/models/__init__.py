# app/models/__init__.py

# Tortoise ORM이 'models' 모듈을 로드할 때
# 아래 파일들이 함께 메모리에 올라가도록 명시적으로 import 해줍니다.
from .game import Game
from .user import User
from .user_game import UserGame

# 외부에서 from app.models import User 처럼 깔끔하게 가져다 쓸 수 있게 됩니다.
__all__ = ["User", "Game", "UserGame"]
