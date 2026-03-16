from __future__ import annotations

import asyncio


def test_health_endpoint_is_registered():
    """앱에 /health 및 /api/health 라우트가 등록되어 있어야 한다."""
    from app.main import app

    paths = {route.path for route in app.routes}  # type: ignore[union-attr]
    assert "/health" in paths
    assert "/api/health" in paths


def test_health_check_returns_ok():
    """/health 핸들러가 status='ok'와 메타데이터를 반환해야 한다."""

    async def _call():
        from app.main import health_check

        return await health_check()

    result = asyncio.run(_call())
    assert result["status"] == "ok"
    assert result["title"] == "모두의마블 API"
    assert result["version"] == "0.1.0"


def test_api_health_check_returns_ok():
    """/api/health 핸들러가 status='ok'를 반환해야 한다."""

    async def _call():
        from app.main import api_health_check

        return await api_health_check()

    result = asyncio.run(_call())
    assert result["status"] == "ok"
