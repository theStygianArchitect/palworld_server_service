import pytest
import httpx
from app.engine import PalEngine


@pytest.mark.asyncio
async def test_pal_engine_defaults():
    engine = PalEngine(admin_password="test_password", rest_port=8212)
    assert engine.admin_password == "test_password"
    assert engine.base_url == "http://127.0.0.1:8212/v1/api"
    assert engine.auth == ("admin", "test_password")


@pytest.mark.asyncio
async def test_pal_engine_check_readiness_offline():
    engine = PalEngine(rest_port=9999)  # Non-existent port
    readiness = await engine.check_readiness()
    assert readiness["ready"] is False
    assert readiness["version"] is None


@pytest.mark.asyncio
async def test_pal_engine_metrics_offline():
    engine = PalEngine(rest_port=9999)
    metrics = await engine.get_engine_metrics()
    assert metrics["server_fps"] == 0
    assert metrics["current_players"] == 0
    assert metrics["max_players"] == 32


@pytest.mark.asyncio
async def test_pal_engine_mock_rest_api_calls(monkeypatch):
    engine = PalEngine(admin_password="test_password", rest_port=8212)

    # Mock announcements
    async def mock_post(url, *args, **kwargs):
        class MockResponse:
            status_code = 200
            def json(self):
                return {"message": "ok"}
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", lambda self, url, *a, **kw: mock_post(url, *a, **kw))

    broadcast_ok = await engine.send_broadcast("Hello World")
    assert broadcast_ok is True

    kick_ok = await engine.kick_player("1001", "Testing kick")
    assert kick_ok is True

    ban_ok = await engine.ban_player("1001", "Testing ban")
    assert ban_ok is True

    unban_ok = await engine.unban_player("1001")
    assert unban_ok is True

    save_ok = await engine.trigger_save()
    assert save_ok is True
