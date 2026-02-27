import socketio
from fastapi import FastAPI

from services.game.api import router as game_api_router
from services.game.sio import sio

fastapi_app = FastAPI()
fastapi_app.include_router(game_api_router)


@fastapi_app.get("/health")
async def health():
    return {"ok": True}


app = socketio.ASGIApp(
    sio,
    other_asgi_app=fastapi_app,
)
