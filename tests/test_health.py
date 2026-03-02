"""
test_health.py — 헬스체크 엔드포인트 테스트
"""

import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    """GET /health가 200과 ok 상태를 반환하는지 확인."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
