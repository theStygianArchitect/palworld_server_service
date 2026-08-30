import pytest
import httpx
from app.notifications import DiscordNotifier


@pytest.mark.asyncio
async def test_discord_notifier_disabled():
    notifier = DiscordNotifier(None)
    assert await notifier.send_embed("Title", "Desc") is False
    assert await notifier.notify_server_ready("Server", "v1.0", "127.0.0.1", 8211) is False
    assert await notifier.notify_reboot_countdown("5 minutes") is False
    assert await notifier.notify_reboot_complete("Server") is False
    assert await notifier.notify_player_action("kick", "1001", "Testing") is False


@pytest.mark.asyncio
async def test_discord_notifier_successful_dispatch(monkeypatch):
    notifier = DiscordNotifier("https://discord.com/api/webhooks/12345/abcdef")

    async def mock_post(url, *args, **kwargs):
        class MockResponse:
            status_code = 204
            text = ""
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", lambda self, url, *a, **kw: mock_post(url, *a, **kw))

    res1 = await notifier.notify_server_ready("Test Server", "1.0", "1.2.3.4", 8211)
    assert res1 is True

    res2 = await notifier.notify_reboot_countdown("10 minutes", is_updating=True, update_tag="v0.2.0")
    assert res2 is True

    res3 = await notifier.notify_reboot_complete("Test Server")
    assert res3 is True

    res4 = await notifier.notify_player_action("ban", "1001", "Cheating")
    assert res4 is True
