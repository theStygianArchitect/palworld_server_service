"""Unit tests for PalEngine lifecycle management, REST API queries, and reboot timers."""
# pylint: disable=missing-function-docstring
# Rationale: Pytest test function names are self-descriptive and documented via assertions.
# pylint: disable=unused-argument
# Rationale: Mock coroutines and monkeypatch fixtures must accept standard signature arguments.

import asyncio

import httpx
import pytest

from app.engine import PalEngine


@pytest.mark.asyncio
async def test_pal_engine_defaults():
    engine = PalEngine(admin_password="test_password", rest_port=8212)  # nosec B105,B106
    assert engine.admin_password == "test_password"  # nosec B105
    assert engine.base_url == "http://127.0.0.1:8212/v1/api"
    assert engine.auth == ("admin", "test_password")  # nosec B105


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
    engine = PalEngine(admin_password="test_password", rest_port=8212)  # nosec B105,B106

    # Mock announcements
    async def mock_post(url, *args, **kwargs):
        # pylint: disable=too-few-public-methods
        # Rationale: Lightweight mock object designed solely to stub HTTP response status.
        class MockResponse:
            """Mock HTTP response."""

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


@pytest.mark.asyncio
async def test_pal_engine_execute_countdown_and_reboot_fast(monkeypatch, tmp_path):
    lock_file = tmp_path / "palworld_reboot.lock"
    update_flag = tmp_path / ".update_requested"

    engine = PalEngine(
        admin_password="test_password",  # nosec B105,B106
        rest_port=8212,
        lock_file=lock_file,
        update_flag=update_flag,
    )

    # Mock instant async sleep so test completes in milliseconds
    async def mock_sleep(*a, **kw):
        return None

    monkeypatch.setattr("app.engine.service.asyncio.sleep", mock_sleep)

    # Mock engine operations
    async def mock_broadcast(*a, **kw):
        return True

    async def mock_save(*a, **kw):
        return True

    async def mock_check_readiness(*a, **kw):
        return {"ready": True, "version": "v0.3.5", "server_name": "Test Server"}

    monkeypatch.setattr(engine, "send_broadcast", mock_broadcast)
    monkeypatch.setattr(engine, "trigger_save", mock_save)
    monkeypatch.setattr(engine, "check_readiness", mock_check_readiness)

    # Execute 2-second fast countdown
    await engine.execute_countdown_and_reboot(
        countdown_seconds=2,
        trigger_update=True,
        update_version_tag="v0.3.5",
        custom_message="Fast test reboot",
    )

    assert engine.lifecycle_state["phase"] == "IDLE"
    assert not lock_file.exists()
    assert update_flag.exists()


@pytest.mark.asyncio
async def test_pal_engine_cancel_countdown(monkeypatch, tmp_path):
    lock_file = tmp_path / "palworld_reboot.lock"
    update_flag = tmp_path / ".update_requested"

    engine = PalEngine(
        admin_password="test_password",  # nosec B105,B106
        rest_port=8212,
        lock_file=lock_file,
        update_flag=update_flag,
    )

    # 1. Cancelling when IDLE returns False
    assert await engine.cancel_countdown(reason="Not running") is False

    broadcasts: list[str] = []

    async def mock_broadcast(msg, mirror_discord=False):
        broadcasts.append(msg)
        return True

    cancelled_notifs: list[tuple[str, str]] = []

    async def mock_notify_cancelled(server_name="", reason=""):
        cancelled_notifs.append((server_name, reason))
        return True

    monkeypatch.setattr(engine, "send_broadcast", mock_broadcast)
    monkeypatch.setattr(engine.notifier, "notify_reboot_cancelled", mock_notify_cancelled)

    # Start countdown in background task
    countdown_task = asyncio.create_task(
        engine.execute_countdown_and_reboot(
            countdown_seconds=10,
            trigger_update=True,
            custom_message="Maintenance test",
        )
    )

    # Yield control until engine enters COUNTDOWN phase
    for _ in range(50):
        if engine.lifecycle_state.get("phase") == "COUNTDOWN":
            break
        await asyncio.sleep(0.01)

    assert engine.lifecycle_state.get("phase") == "COUNTDOWN"
    assert lock_file.exists()
    assert update_flag.exists()

    # Cancel countdown
    cancelled = await engine.cancel_countdown(reason="Boss raid active")
    assert cancelled is True

    # Await completion of countdown task
    await countdown_task

    # Verify post-cancellation cleanup
    assert engine.lifecycle_state.get("phase") == "IDLE"
    assert not lock_file.exists()
    assert not update_flag.exists()
    assert any("CANCELLED: Boss raid active" in b for b in broadcasts)
    assert len(cancelled_notifs) == 1
    assert cancelled_notifs[0][1] == "Boss raid active"
