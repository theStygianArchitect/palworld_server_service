"""Palworld Engine Management & Lifecycle Orchestrator.

Manages REST API interactions, RCON commands, systemd lifecycle states,
declarative reboot countdown warnings, and Discord notification mirroring
in accordance with Google Style Guide and 3 AM standards.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import WebSocket

from .logger import log
from .notifications import DiscordNotifier
from .tracker import CommunityTracker
from .types import EngineMetrics, LifecycleState, ReadinessInfo


def _resolve_default_ini_path() -> Path:
    """Finds the active PalWorldSettings.ini across standard Steam and custom directories."""
    candidate_paths = [
        Path("/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/home/steam/.steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path(
            "/home/steam/.local/share/Steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
        ),
        Path("/home/steam/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/opt/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path.home() / ".palmanager" / "PalWorldSettings.ini",
    ]
    for p in candidate_paths:
        try:
            if p.is_file():
                return p
        except PermissionError as err:
            log.debug("Permission error checking candidate path %s: %s", p, err)
        except OSError as err:
            log.debug("OS error checking candidate path %s: %s", p, err)

    if os.name != "nt":
        if Path("/home/steam/.steam/steam/steamapps").exists():
            return candidate_paths[0]
        if Path("/home/steam/.steam/steamapps").exists():
            return candidate_paths[1]
        if Path("/home/steam").exists():
            return candidate_paths[0]
    return Path.home() / ".palmanager" / "PalWorldSettings.ini"


def _resolve_default_update_flag() -> Path:
    """Returns the production steam update flag path if accessible, else falls back to ~/.palmanager."""
    steam_flag = Path("/home/steam/.update_requested")
    if os.name != "nt" and Path("/home/steam").exists():
        return steam_flag
    return Path.home() / ".palmanager" / ".update_requested"


DEFAULT_INI_PATH: Path = _resolve_default_ini_path()
DEFAULT_SERVICE_NAME: str = "palworld.service"
DEFAULT_UPDATE_FLAG: Path = _resolve_default_update_flag()
DEFAULT_LOCK_FILE: Path = Path(tempfile.gettempdir()) / "palworld_reboot.lock"

COUNTDOWN_DISCORD_INTERVALS: set[int] = {600, 300, 60}
COUNTDOWN_ALL_INTERVALS: set[int] = {600, 300, 180, 120, 60, 30, 15, 10, 5, 4, 3, 2, 1}


class PalEngine:
    """Core orchestrator for Palworld REST API, systemd operations, and reboot lifecycle.

    Attributes:
        admin_password (str): Server administrator password for REST authentication.
        rest_port (int): Listening port for internal REST API.
        server_name (str): Display name for the dedicated server.
        domain (str): Configured public domain name for announcements.
        ini_path (Path): Path to active PalWorldSettings.ini file.
        service_name (str): Systemd service unit name.
        update_flag (Path): Filepath flag indicating SteamCMD update is requested.
        lock_file (Path): Filepath lock preventing overlapping reboots.
        base_url (str): Base HTTP URL for local REST API endpoint.
        auth (tuple[str, str]): Basic auth credentials ('admin', password).
        active_sockets (set[WebSocket]): Set of active client WebSocket connections.
        tracker (CommunityTracker): Telemetry and discovery hub tracker.
        notifier (DiscordNotifier): Webhook notification dispatcher.
        lifecycle_state (LifecycleState): Current active lifecycle and reboot progress state.
    """

    def __init__(
        self,
        admin_password: str | None = None,
        rest_port: int | None = None,
        server_name: str | None = None,
        domain: str | None = None,
        discord_webhook_url: str | None = None,
        ini_path: str | Path | None = None,
        service_name: str | None = None,
        update_flag: str | Path | None = None,
        lock_file: str | Path | None = None,
    ) -> None:
        """Initializes the PalEngine orchestrator.

        Args:
            admin_password (str | None): Admin password for REST authentication.
            rest_port (int | None): REST API port (default: 8212).
            server_name (str | None): Dedicated server name.
            domain (str | None): Public domain hostname.
            discord_webhook_url (str | None): Discord incoming webhook URL.
            ini_path (str | Path | None): Filepath to PalWorldSettings.ini.
            service_name (str | None): Systemd service unit name.
            update_flag (str | Path | None): SteamCMD update flag path.
            lock_file (str | Path | None): Reboot lock file path.

        Raises:
            ValueError: If service_name contains illegal shell characters.
        """
        self.admin_password: str = admin_password or os.getenv("PALWORLD_ADMIN_PASSWORD") or "admin_password"
        self.rest_port: int = rest_port or int(os.getenv("PALWORLD_REST_PORT", "8212"))
        self.server_name: str = server_name or os.getenv("PALWORLD_SERVER_NAME") or "Palworld Dedicated Server"
        self.domain: str = domain or os.getenv("PALWORLD_SERVER_DOMAIN") or "yourdomain.duckdns.org"

        self.ini_path: Path = Path(ini_path) if ini_path else DEFAULT_INI_PATH
        raw_svc = service_name or os.getenv("PALWORLD_SERVICE_NAME") or DEFAULT_SERVICE_NAME
        if not re.match(r"^[a-zA-Z0-9_.\-]+$", raw_svc):
            raise ValueError(f"Invalid service_name format: {raw_svc}")
        self.service_name: str = raw_svc
        self.update_flag: Path = Path(update_flag) if update_flag else DEFAULT_UPDATE_FLAG
        self.lock_file: Path = Path(lock_file) if lock_file else DEFAULT_LOCK_FILE

        self.base_url: str = f"http://127.0.0.1:{self.rest_port}/v1/api"
        self.auth: tuple[str, str] = ("admin", self.admin_password)
        self.active_sockets: set[WebSocket] = set()

        self.tracker: CommunityTracker = CommunityTracker(self.server_name, self.domain)
        self.notifier: DiscordNotifier = DiscordNotifier(
            discord_webhook_url or os.getenv("PALWORLD_DISCORD_WEBHOOK_URL")
        )
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

        self.lifecycle_state: LifecycleState = {
            "phase": "IDLE",
            "remaining_seconds": 0,
            "total_seconds": 0,
            "current_broadcast": "",
            "is_updating": False,
        }

    async def register_socket(self, ws: WebSocket) -> None:
        """Registers an active client WebSocket connection for real-time telemetry.

        Args:
            ws (WebSocket): Incoming connected client WebSocket instance.
        """
        await ws.accept()
        self.active_sockets.add(ws)

    def unregister_socket(self, ws: WebSocket) -> None:
        """Removes a disconnected client WebSocket.

        Args:
            ws (WebSocket): Disconnected client WebSocket instance.
        """
        self.active_sockets.discard(ws)

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        """Broadcasts a JSON telemetry payload to all connected frontend clients.

        Args:
            message (dict[str, Any]): Telemetry payload dictionary to serialize and send.
        """
        dead_sockets: set[WebSocket] = set()
        for ws in self.active_sockets:
            try:
                await ws.send_json(message)
            except Exception as err:
                log.debug("WebSocket client disconnected or send failed: %s", err)
                dead_sockets.add(ws)
        self.active_sockets -= dead_sockets

    async def check_readiness(self) -> ReadinessInfo:
        """Probes the Palworld internal REST API for operational readiness.

        Returns:
            ReadinessInfo: Ready boolean flag, reported build version, and server name.
        """
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
        except httpx.TimeoutException as err:
            log.debug("Engine readiness probe timed out: %s", err)
        except httpx.ConnectError as err:
            log.debug("Engine readiness probe connection refused (server likely offline/booting): %s", err)
        except httpx.HTTPError as err:
            log.debug("Engine readiness probe HTTP error: %s", err)
        return {"ready": False, "version": None, "server_name": self.server_name}

    async def get_engine_metrics(self) -> EngineMetrics:
        """Fetches live server FPS, frame time, uptime, and player count from REST API.

        Returns:
            EngineMetrics: Server tick rate, tick time in ms, uptime, and player numbers.
        """
        metrics: EngineMetrics = {
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
        except httpx.TimeoutException as err:
            log.debug("Engine metrics request timed out: %s", err)
        except httpx.ConnectError as err:
            log.debug("Engine metrics request connection refused: %s", err)
        except httpx.HTTPError as err:
            log.debug("Engine metrics request HTTP error: %s", err)
        return metrics

    async def get_raw_players(self) -> list[dict[str, Any]]:
        """Retrieves raw online player list from REST API.

        Returns:
            list[dict[str, Any]]: List of connected player records.
        """
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.base_url}/players", auth=self.auth)
                if res.status_code == 200:
                    players = res.json().get("players", [])
                    if isinstance(players, list):
                        return players
        except httpx.TimeoutException as err:
            log.debug("Player list request timed out: %s", err)
        except httpx.ConnectError as err:
            log.debug("Player list request connection refused: %s", err)
        except httpx.HTTPError as err:
            log.debug("Player list request HTTP error: %s", err)
        return []

    async def send_broadcast(self, message: str, mirror_discord: bool = True) -> bool:
        """Broadcasts an announcement banner across the in-game HUD and optionally echoes to Discord.

        Args:
            message (str): Announcement text to display.
            mirror_discord (bool): Whether to dispatch a Discord embed (default: True).

        Returns:
            bool: True if broadcast succeeded in-game or mirrored to Discord.
        """
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
        except httpx.TimeoutException as err:
            log.debug("Broadcast notice timed out: %s", err)
            if mirror_discord:
                await self.notifier.notify_admin_broadcast(self.server_name or "Palworld Server", message)
            return False
        except httpx.ConnectError as err:
            log.debug("Broadcast notice connection error: %s", err)
            if mirror_discord:
                await self.notifier.notify_admin_broadcast(self.server_name or "Palworld Server", message)
            return False
        except httpx.HTTPError as err:
            log.debug("Broadcast notice HTTP failure: %s", err)
            if mirror_discord:
                await self.notifier.notify_admin_broadcast(self.server_name or "Palworld Server", message)
            return False

    async def kick_player(self, player_id: str, message: str = "Kicked by administrator") -> bool:
        """Kicks a player from the server via REST API and logs an audit embed to Discord.

        Args:
            player_id (str): Target player ID or platform account ID.
            message (str): Reason text sent to the kicked player.

        Returns:
            bool: True if kick command succeeded.
        """
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
        except httpx.TimeoutException as err:
            log.warning("Kick player %s timed out: %s", player_id, err)
            return False
        except httpx.ConnectError as err:
            log.warning("Kick player %s connection error: %s", player_id, err)
            return False
        except httpx.HTTPError as err:
            log.warning("Failed to kick player %s: %s", player_id, err)
            return False

    async def ban_player(self, player_id: str, message: str = "Banned by administrator") -> bool:
        """Bans a player from the server via REST API and logs an audit embed to Discord.

        Args:
            player_id (str): Target player ID or platform account ID.
            message (str): Reason text recorded for ban.

        Returns:
            bool: True if ban command succeeded.
        """
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
        except httpx.TimeoutException as err:
            log.warning("Ban player %s timed out: %s", player_id, err)
            return False
        except httpx.ConnectError as err:
            log.warning("Ban player %s connection error: %s", player_id, err)
            return False
        except httpx.HTTPError as err:
            log.warning("Failed to ban player %s: %s", player_id, err)
            return False

    async def unban_player(self, player_id: str) -> bool:
        """Unbans a player from the server via REST API.

        Args:
            player_id (str): Target player ID to unban.

        Returns:
            bool: True if unban command succeeded.
        """
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(
                    f"{self.base_url}/unban",
                    auth=self.auth,
                    json={"userid": player_id},
                )
                return res.status_code == 200
        except httpx.TimeoutException as err:
            log.warning("Unban player %s timed out: %s", player_id, err)
            return False
        except httpx.ConnectError as err:
            log.warning("Unban player %s connection error: %s", player_id, err)
            return False
        except httpx.HTTPError as err:
            log.warning("Failed to unban player %s: %s", player_id, err)
            return False

    async def trigger_save(self) -> bool:
        """Triggers an in-engine world save via REST API.

        Returns:
            bool: True if world save completed successfully.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(f"{self.base_url}/save", auth=self.auth)
                return res.status_code == 200
        except httpx.TimeoutException as err:
            log.warning("World save request timed out: %s", err)
            return False
        except httpx.ConnectError as err:
            log.warning("World save connection failed: %s", err)
            return False
        except httpx.HTTPError as err:
            log.warning("World save failed: %s", err)
            return False

    @staticmethod
    def format_countdown_string(remaining_seconds: int) -> str:
        """Formats remaining countdown seconds into human-readable string.

        Args:
            remaining_seconds (int): Integer count of seconds remaining.

        Returns:
            str: Human-friendly duration string (e.g. '5 minutes' or '30 seconds').
        """
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
        """Executes a linear, atomic reboot countdown sequence with in-game and Discord notifications.

        Args:
            countdown_seconds (int): Seconds duration before restarting (default: 60).
            trigger_update (bool): Whether to touch SteamCMD update flag before restart.
            update_version_tag (str): Target version string if updating.
            custom_message (str): Optional administrator announcement note.

        Raises:
            RuntimeError: If a reboot countdown sequence is already active.
        """
        if self.lock_file.exists():
            try:
                stale_age = time.time() - self.lock_file.stat().st_mtime
                if stale_age > 900:  # 15 minutes TTL
                    log.warning(
                        "Found stale reboot lock file (%s) older than 15m (age: %.1fs). Auto-reclaiming lock.",
                        self.lock_file,
                        stale_age,
                    )
                    self.lock_file.unlink(missing_ok=True)
            except PermissionError as err:
                log.warning("Permission denied inspecting reboot lock file %s: %s", self.lock_file, err)
                if self.lock_file.exists():
                    raise RuntimeError("Reboot countdown sequence is already active.") from err
            except OSError as err:
                log.warning("OS error inspecting reboot lock file %s: %s", self.lock_file, err)
                if self.lock_file.exists():
                    raise RuntimeError("Reboot countdown sequence is already active.") from err

        async with self._lifecycle_lock:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            self.lock_file.touch()

            try:
                if trigger_update:
                    self.update_flag.parent.mkdir(parents=True, exist_ok=True)
                    self.update_flag.touch()

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
                    try:
                        sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"
                        systemctl_bin = shutil.which("systemctl") or "/bin/systemctl"
                        subprocess.run([sudo_bin, systemctl_bin, "restart", self.service_name], check=False)
                    except OSError as err:
                        log.error("Failed to execute systemctl restart: %s", err)

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
                if self.lock_file.exists():
                    try:
                        self.lock_file.unlink()
                    except OSError as err:
                        log.warning("Could not unlink lock file %s: %s", self.lock_file, err)

                self.lifecycle_state = {
                    "phase": "IDLE",
                    "remaining_seconds": 0,
                    "total_seconds": 0,
                    "current_broadcast": "",
                    "is_updating": False,
                }
                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})


LOCK_FILE: Path = DEFAULT_LOCK_FILE
