"""Palworld Engine Management & Lifecycle Orchestrator.

Manages REST API interactions, RCON commands, systemd lifecycle states,
declarative reboot countdown warnings, and Discord notification mirroring.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import WebSocket

from .logger import log
from .notifications import DiscordNotifier
from .tracker import CommunityTracker

DEFAULT_INI_PATH = (
    "/home/steam/.steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
    if os.name != "nt"
    else os.path.expanduser("~/.palmanager/PalWorldSettings.ini")
)
DEFAULT_SERVICE_NAME = "palworld.service"
DEFAULT_UPDATE_FLAG = (
    "/home/steam/.update_requested" if os.name != "nt" else os.path.expanduser("~/.palmanager/.update_requested")
)
DEFAULT_LOCK_FILE = os.path.join(tempfile.gettempdir(), "palworld_reboot.lock")

# Declarative 3 AM countdown alert schedule
COUNTDOWN_DISCORD_INTERVALS: set[int] = {600, 300, 60}
COUNTDOWN_ALL_INTERVALS: set[int] = {600, 300, 180, 120, 60, 30, 15, 10, 5, 4, 3, 2, 1}


class PalEngine:
    """Core orchestrator for Palworld REST API, systemd operations, and reboot lifecycle."""

    def __init__(
        self,
        admin_password: str | None = None,
        rest_port: int | None = None,
        server_name: str | None = None,
        domain: str | None = None,
        discord_webhook_url: str | None = None,
        ini_path: str | None = None,
        service_name: str | None = None,
        update_flag: str | None = None,
        lock_file: str | None = None,
    ) -> None:
        self.admin_password: str = admin_password or os.getenv("PALWORLD_ADMIN_PASSWORD") or "admin_password"
        self.rest_port: int = rest_port or int(os.getenv("PALWORLD_REST_PORT", "8212"))
        self.server_name: str = server_name or os.getenv("PALWORLD_SERVER_NAME") or "Palworld Dedicated Server"
        self.domain: str = domain or os.getenv("PALWORLD_SERVER_DOMAIN") or "yourdomain.duckdns.org"

        self.ini_path: str = ini_path or os.getenv("PALWORLD_INI_PATH") or DEFAULT_INI_PATH
        self.service_name: str = service_name or os.getenv("PALWORLD_SERVICE_NAME") or DEFAULT_SERVICE_NAME
        self.update_flag: str = update_flag or os.getenv("PALWORLD_UPDATE_FLAG") or DEFAULT_UPDATE_FLAG
        self.lock_file: str = lock_file or os.getenv("PALWORLD_LOCK_FILE") or DEFAULT_LOCK_FILE

        self.base_url = f"http://127.0.0.1:{self.rest_port}/v1/api"
        self.auth = ("admin", self.admin_password)
        self.active_sockets: set[WebSocket] = set()

        self.tracker = CommunityTracker(self.server_name, self.domain)
        self.notifier = DiscordNotifier(discord_webhook_url or os.getenv("PALWORLD_DISCORD_WEBHOOK_URL"))
        self._lifecycle_lock = asyncio.Lock()

        self.lifecycle_state: dict[str, Any] = {
            "phase": "IDLE",
            "remaining_seconds": 0,
            "total_seconds": 0,
            "current_broadcast": "",
            "is_updating": False,
        }

    async def register_socket(self, ws: WebSocket) -> None:
        """Registers an active client WebSocket connection for real-time telemetry."""
        await ws.accept()
        self.active_sockets.add(ws)

    def unregister_socket(self, ws: WebSocket) -> None:
        """Removes a disconnected client WebSocket."""
        self.active_sockets.discard(ws)

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        """Broadcasts a JSON telemetry payload to all connected frontend clients."""
        dead_sockets = set()
        for ws in self.active_sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead_sockets.add(ws)
        self.active_sockets -= dead_sockets

    async def check_readiness(self) -> dict[str, Any]:
        """Probes the Palworld internal REST API for operational readiness."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.base_url}/info", auth=self.auth)
                if res.status_code == 200:
                    info = res.json()
                    return {
                        "ready": True,
                        "version": info.get("version"),
                        "server_name": info.get("servername", self.server_name),
                    }
        except httpx.HTTPError:
            pass
        return {"ready": False, "version": None, "server_name": self.server_name}

    async def get_engine_metrics(self) -> dict[str, Any]:
        """Fetches live server FPS, frame time, uptime, and player count from REST API."""
        metrics = {
            "server_fps": 0,
            "server_frame_time_ms": 0.0,
            "uptime_seconds": 0,
            "days": 0,
            "current_players": 0,
            "max_players": 32,
        }
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.base_url}/metrics", auth=self.auth)
                if res.status_code == 200:
                    data = res.json()
                    metrics["server_fps"] = data.get("serverfps", 0)
                    metrics["server_frame_time_ms"] = round(float(data.get("serverframetime", 0.0)), 2)
                    metrics["uptime_seconds"] = data.get("uptime", 0)
                    metrics["days"] = data.get("days", 0)
                    metrics["current_players"] = data.get("currentplayernum", 0)
                    metrics["max_players"] = data.get("maxplayernum", 32)
        except httpx.HTTPError:
            pass
        return metrics

    async def get_raw_players(self) -> list[dict[str, Any]]:
        """Retrieves raw online player list from REST API."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.base_url}/players", auth=self.auth)
                if res.status_code == 200:
                    return res.json().get("players", [])
        except httpx.HTTPError:
            pass
        return []

    async def send_broadcast(self, message: str, mirror_discord: bool = True) -> bool:
        """Broadcasts an announcement banner across the in-game HUD and optionally echoes to Discord."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(
                    f"{self.base_url}/announce",
                    auth=self.auth,
                    json={"message": message},
                )
                if mirror_discord:
                    await self.notifier.notify_admin_broadcast(self.server_name or "Palworld Server", message)
                return res.status_code == 200
        except httpx.HTTPError as e:
            log.debug("Broadcast notice HTTP error: %s", e)
            if mirror_discord:
                await self.notifier.notify_admin_broadcast(self.server_name or "Palworld Server", message)
            return False

    async def kick_player(self, player_id: str, message: str = "Kicked by administrator") -> bool:
        """Kicks a player from the server via REST API and logs an audit embed to Discord."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(
                    f"{self.base_url}/kick",
                    auth=self.auth,
                    json={"userid": player_id, "message": message},
                )
                success = res.status_code == 200
                if success:
                    await self.notifier.notify_player_action("kick", player_id, message)
                return success
        except httpx.HTTPError as e:
            log.warning("Failed to kick player %s: %s", player_id, e)
            return False

    async def ban_player(self, player_id: str, message: str = "Banned by administrator") -> bool:
        """Bans a player from the server via REST API and logs an audit embed to Discord."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(
                    f"{self.base_url}/ban",
                    auth=self.auth,
                    json={"userid": player_id, "message": message},
                )
                success = res.status_code == 200
                if success:
                    await self.notifier.notify_player_action("ban", player_id, message)
                return success
        except httpx.HTTPError as e:
            log.warning("Failed to ban player %s: %s", player_id, e)
            return False

    async def unban_player(self, player_id: str) -> bool:
        """Unbans a player from the server via REST API."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(
                    f"{self.base_url}/unban",
                    auth=self.auth,
                    json={"userid": player_id},
                )
                return res.status_code == 200
        except httpx.HTTPError as e:
            log.warning("Failed to unban player %s: %s", player_id, e)
            return False

    async def trigger_save(self) -> bool:
        """Triggers an in-engine world save via REST API."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(f"{self.base_url}/save", auth=self.auth)
                return res.status_code == 200
        except httpx.HTTPError as e:
            log.warning("World save failed: %s", e)
            return False

    @staticmethod
    def format_countdown_string(remaining_seconds: int) -> str:
        """Formats remaining countdown seconds into human-readable string."""
        if remaining_seconds >= 60:
            mins = remaining_seconds // 60
            return f"{mins} minute{'s' if mins > 1 else ''}"
        return f"{remaining_seconds} seconds"

    async def execute_countdown_and_reboot(
        self,
        countdown_seconds: int = 60,
        trigger_update: bool = False,
        update_version_tag: str = "",
        custom_message: str = "",
    ) -> None:
        """Executes a linear, atomic reboot countdown sequence with in-game and Discord notifications."""
        if os.path.exists(self.lock_file):
            raise RuntimeError("Reboot countdown sequence is already active.")

        async with self._lifecycle_lock:
            Path(self.lock_file).parent.mkdir(parents=True, exist_ok=True)
            Path(self.lock_file).touch()

            try:
                if trigger_update:
                    Path(self.update_flag).parent.mkdir(parents=True, exist_ok=True)
                    Path(self.update_flag).touch()

                self.lifecycle_state = {
                    "phase": "COUNTDOWN",
                    "remaining_seconds": countdown_seconds,
                    "total_seconds": countdown_seconds,
                    "current_broadcast": custom_message or "Initiating countdown sequence...",
                    "is_updating": trigger_update,
                }
                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

                remaining = countdown_seconds
                while remaining > 0:
                    self.lifecycle_state["remaining_seconds"] = remaining

                    if remaining in COUNTDOWN_ALL_INTERVALS or remaining == countdown_seconds:
                        time_str = self.format_countdown_string(remaining)

                        base_msg = f"Server maintenance restart in {time_str}."
                        if trigger_update:
                            target_info = f" to {update_version_tag}" if update_version_tag else ""
                            base_msg = f"Server updating{target_info} and restarting in {time_str}."

                        full_msg = f"{custom_message} - {base_msg}" if custom_message else base_msg
                        self.lifecycle_state["current_broadcast"] = full_msg

                        await self.send_broadcast(full_msg, mirror_discord=False)

                        if remaining in COUNTDOWN_DISCORD_INTERVALS or remaining == countdown_seconds:
                            await self.notifier.notify_reboot_countdown(
                                time_str,
                                trigger_update,
                                update_version_tag,
                                custom_message=custom_message,
                            )

                    await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})
                    await asyncio.sleep(1)
                    remaining -= 1

                # 1. World Save Phase
                self.lifecycle_state["phase"] = "SAVING"
                self.lifecycle_state["current_broadcast"] = "Saving world state to disk..."
                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})
                await self.send_broadcast("Server restarting NOW. Saving progress.")
                await self.trigger_save()
                await asyncio.sleep(1)

                # 2. Systemctl Restart Phase
                self.lifecycle_state["phase"] = "MAINTENANCE"
                self.lifecycle_state["current_broadcast"] = "Executing systemctl restart & backup hooks..."
                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

                if os.name != "nt":
                    log.info("Triggering systemctl restart for %s", self.service_name)
                    subprocess.run(["sudo", "/bin/systemctl", "restart", self.service_name], check=False)

                # 3. Probing Readiness Phase
                self.lifecycle_state["phase"] = "PROBING"
                self.lifecycle_state["current_broadcast"] = "Probing engine initialization and readiness..."
                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

                for _ in range(90):
                    ready_check = await self.check_readiness()
                    if ready_check["ready"]:
                        log.info("Server engine restored to ready state.")
                        await self.notifier.notify_reboot_complete(self.server_name or "Palworld Server")
                        break
                    await asyncio.sleep(2)

            finally:
                if os.path.exists(self.lock_file):
                    os.remove(self.lock_file)
                self.lifecycle_state = {
                    "phase": "IDLE",
                    "remaining_seconds": 0,
                    "total_seconds": 0,
                    "current_broadcast": "",
                    "is_updating": False,
                }
                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})


LOCK_FILE = DEFAULT_LOCK_FILE
