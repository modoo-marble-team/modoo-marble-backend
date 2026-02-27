from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(prefix="/api/game", tags=["game"])


@router.get("/test")
async def game_test_html():
    file_path = Path(__file__).resolve().parent / "test.html"
    return FileResponse(file_path)
