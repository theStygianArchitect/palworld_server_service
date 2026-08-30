"""Discord webhook notification dispatcher and embed formatter.

Provides asynchronous, rich-embed status dispatching for server readiness,
reboot countdown warnings, administrative in-game broadcasts, and player moderation events.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

import httpx

from .logger import log


class DiscordNotifier:
    """Dispatches formatted notifications to Discord via incoming webhooks.

    Attributes:
        webhook_url (str | None): Configured Discord incoming webhook endpoint URL.
    """

    COLOR_INFO: int = 0x3B82F6  # Blue
    COLOR_SUCCESS: int = 0x10B981  # Emerald Green
    COLOR_WARNING: int = 0xF59E0B  # Amber / Orange
    COLOR_ALERT: int = 0xEF4444  # Red
    COLOR_BROADCAST: int = 0x8B5CF6  # Purple

    def __init__(self, webhook_url: str | None = None) -> None:
        """Initializes the Discord notifier.

        Args:
            webhook_url (str | None): Target Discord incoming webhook URL.
        """
        self.webhook_url = webhook_url

    async def send_embed(
        self,
        title: str,
        description: str,
        color: int = COLOR_INFO,
        fields: list[dict[str, Any]] | None = None,
        footer: str = "Palworld Operations Suite",
    ) -> bool:
        """Dispatches an asynchronous rich embed payload to the configured webhook.

        Args:
            title (str): Title header of the Discord embed.
            description (str): Main body markdown text of the embed.
            color (int): Hexadecimal RGB color integer (default: COLOR_INFO).
            fields (list[dict[str, Any]] | None): Optional list of field objects with name/value/inline.
            footer (str): Footer text displayed at the bottom of the embed.

        Returns:
            bool: True if Discord returned HTTP 200 or 204 status, False otherwise.
        """
        if not self.webhook_url or not self.webhook_url.strip().startswith("https://discord.com/api/webhooks/"):
            return False

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                    "fields": fields or [],
                    "footer": {"text": footer},
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.post(self.webhook_url, json=payload)
                if res.status_code in {200, 204}:
                    return True
                if res.status_code == 429:
                    try:
                        retry_data = res.json()
                        retry_after = float(retry_data.get("retry_after", 1.5))
                    except ValueError as err:
                        log.debug("Discord rate limit JSON/float parsing error: %s", err)
                        retry_after = 2.0
                    except KeyError as err:
                        log.debug("Discord rate limit response missing retry_after key: %s", err)
                        retry_after = 2.0
                    log.warning("Discord webhook rate-limited (HTTP 429). Retrying after %.1fs...", retry_after)
                    await asyncio.sleep(retry_after)
                    res_retry = await client.post(self.webhook_url, json=payload)
                    return res_retry.status_code in {200, 204}
                log.warning("Discord webhook responded with status %s: %s", res.status_code, res.text)
        except httpx.TimeoutException as err:
            log.warning("Discord webhook request timed out: %s", err)
        except httpx.ConnectError as err:
            log.warning("Discord webhook connection error: %s", err)
        except httpx.HTTPError as err:
            log.warning("Discord webhook HTTP failure: %s", err)
        return False

    async def notify_admin_broadcast(self, server_name: str, message: str) -> bool:
        """Mirrors in-game admin broadcast notices directly to Discord chat.

        Args:
            server_name (str): Server display name for embed footer.
            message (str): Raw broadcast message string.

        Returns:
            bool: True if successfully delivered to Discord.
        """
        clean_msg = message.replace("[ADMIN NOTICE]", "").strip()
        return await self.send_embed(
            title="📢 In-Game Admin Announcement",
            description=f"**{clean_msg}**",
            color=self.COLOR_BROADCAST,
            footer=f"Server: {server_name or 'Palworld Dedicated Server'}",
        )

    async def notify_server_ready(self, server_name: str, version: str, public_ip: str, port: int = 8211) -> bool:
        """Sends an online status announcement when the dedicated server finishes booting.

        Args:
            server_name (str): Dedicated server display name.
            version (str): Reported game engine build version string.
            public_ip (str): Public WAN IP or domain name for direct connecting.
            port (int): UDP game connection port (default: 8211).

        Returns:
            bool: True if successfully delivered to Discord.
        """
        fields = [
            {"name": "Server Name", "value": server_name or "Palworld Dedicated Server", "inline": True},
            {"name": "Engine Version", "value": version or "Live", "inline": True},
            {
                "name": "Direct Connect",
                "value": f"`{public_ip}:{port}`" if public_ip else f"`Port {port}`",
                "inline": False,
            },
        ]
        return await self.send_embed(
            title="🟢 Palworld Server Online & Ready",
            description="The dedicated server has successfully initialized and is accepting player connections.",
            color=self.COLOR_SUCCESS,
            fields=fields,
        )

    async def notify_reboot_countdown(
        self,
        time_remaining_str: str,
        is_updating: bool = False,
        update_tag: str = "",
        custom_message: str = "",
    ) -> bool:
        """Dispatches progressive reboot countdown warnings to the Discord channel.

        Args:
            time_remaining_str (str): Human-readable duration string (e.g. '10 minutes').
            is_updating (bool): Whether a SteamCMD update will occur during restart.
            update_tag (str): Target version string if updating.
            custom_message (str): Optional custom announcement note from administrator.

        Returns:
            bool: True if successfully delivered to Discord.
        """
        update_text = (
            f" (Updating to {update_tag})"
            if (is_updating and update_tag)
            else (" (with SteamCMD update)" if is_updating else "")
        )
        desc = (
            f"Server restart scheduled in **{time_remaining_str}**{update_text}."
            " Please find a safe location to prevent data loss."
        )
        if custom_message:
            desc = f"**Reason:** {custom_message}\n\n{desc}"

        return await self.send_embed(
            title="⚠️ Server Reboot in Progress",
            description=desc,
            color=self.COLOR_WARNING,
        )

    async def notify_reboot_complete(self, server_name: str) -> bool:
        """Sends a recovery notice after a server reboot cycle finishes successfully.

        Args:
            server_name (str): Dedicated server display name.

        Returns:
            bool: True if successfully delivered to Discord.
        """
        return await self.send_embed(
            title="🔄 Server Maintenance Complete",
            description=f"**{server_name or 'Palworld Server'}** has completed its restart cycle and is back online.",
            color=self.COLOR_SUCCESS,
        )

    async def notify_player_action(self, action: str, player_id: str, reason: str = "") -> bool:
        """Logs an administrative player moderation action (kick/ban) to Discord.

        Args:
            action (str): Moderation action name ('kick' or 'ban').
            player_id (str): Target player ID or platform identifier.
            reason (str): Optional administrator reason string.

        Returns:
            bool: True if successfully delivered to Discord.
        """
        fields = [
            {"name": "Action", "value": action.upper(), "inline": True},
            {"name": "Player ID / SteamID", "value": f"`{player_id}`", "inline": True},
        ]
        if reason:
            fields.append({"name": "Reason", "value": reason, "inline": False})

        return await self.send_embed(
            title=f"🛡️ Admin Action: {action.upper()}",
            description=f"Administrative moderation action executed on player `{player_id}`.",
            color=self.COLOR_ALERT if action.lower() == "ban" else self.COLOR_WARNING,
            fields=fields,
        )
