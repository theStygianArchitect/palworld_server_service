import datetime
from typing import Any

import httpx

from .logger import log


class DiscordNotifier:
    """Dispatches formatted notifications to Discord via incoming webhooks."""

    # Embed Colors
    COLOR_INFO = 0x3B82F6  # Blue
    COLOR_SUCCESS = 0x10B981  # Emerald Green
    COLOR_WARNING = 0xF59E0B  # Amber / Orange
    COLOR_ALERT = 0xEF4444  # Red
    COLOR_BROADCAST = 0x8B5CF6  # Purple

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url

    async def send_embed(
        self,
        title: str,
        description: str,
        color: int = COLOR_INFO,
        fields: list[dict[str, Any]] | None = None,
        footer: str = "Palworld Operations Suite",
    ) -> bool:
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
                log.warning(f"Discord webhook responded with status {res.status_code}: {res.text}")
        except Exception as e:
            log.warning(f"Failed to deliver Discord notification: {e}")
        return False

    async def notify_admin_broadcast(self, server_name: str, message: str) -> bool:
        """Mirrors in-game admin broadcast notices directly to Discord chat."""
        clean_msg = message.replace("[ADMIN NOTICE]", "").strip()
        return await self.send_embed(
            title="📢 In-Game Admin Announcement",
            description=f"**{clean_msg}**",
            color=self.COLOR_BROADCAST,
            footer=f"Server: {server_name or 'Palworld Dedicated Server'}",
        )

    async def notify_server_ready(self, server_name: str, version: str, public_ip: str, port: int = 8211) -> bool:
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
        return await self.send_embed(
            title="🔄 Server Maintenance Complete",
            description=f"**{server_name or 'Palworld Server'}** has completed its restart cycle and is back online.",
            color=self.COLOR_SUCCESS,
        )

    async def notify_player_action(self, action: str, player_id: str, reason: str = "") -> bool:
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
