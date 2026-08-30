from unittest.mock import AsyncMock, patch

import pytest

from app.notifications import DiscordNotifier


@pytest.mark.asyncio
async def test_discord_notifier_disabled():
    notifier = DiscordNotifier(webhook_url=None)
    sent = await notifier.send_embed("Test Title", "Test Description")
    assert sent is False


@pytest.mark.asyncio
async def test_discord_notifier_successful_dispatch():
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123456/dummy_token")

    fake_response = AsyncMock()
    fake_response.status_code = 204

    with patch("httpx.AsyncClient.post", return_value=fake_response):
        sent = await notifier.notify_server_ready("Test Server", "0.2.4.0", "1.2.3.4", 8211)
        assert sent is True

        reboot_sent = await notifier.notify_reboot_countdown(
            "10 minutes",
            is_updating=False,
            custom_message="Applying scheduled performance optimizations.",
        )
        assert reboot_sent is True

        broadcast_sent = await notifier.notify_admin_broadcast(
            "Test Server",
            "[ADMIN NOTICE] Server event starting in 5 minutes!",
        )
        assert broadcast_sent is True

        action_sent = await notifier.notify_player_action("kick", "1001", "Speed hacking")
        assert action_sent is True
