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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.config import resolve_palworld_ini_path
from app.core.logger import log
from app.core.types import EngineMetrics, LifecycleState, ReadinessInfo
from app.monitoring.tracker import CommunityTracker

from .notifications import DiscordNotifier


def _resolve_default_update_flag() -> Path:
    """Returns the production steam update flag path, prioritizing /var/lib/palmanager then /home/steam."""
    if os.name != "nt":
        palmanager_flag = Path("/var/lib/palmanager/update_requested")
        if palmanager_flag.parent.exists():
            return palmanager_flag
        steam_flag = Path("/home/steam/.update_requested")
        if steam_flag.parent.exists():
            return steam_flag
    return Path.home() / ".palmanager" / ".update_requested"


DEFAULT_INI_PATH: Path = resolve_palworld_ini_path()
DEFAULT_SERVICE_NAME: str = "palworld.service"
DEFAULT_UPDATE_FLAG: Path = _resolve_default_update_flag()
DEFAULT_LOCK_FILE: Path = Path(tempfile.gettempdir()) / "palworld_reboot.lock"
LOCK_FILE: Path = DEFAULT_LOCK_FILE

COUNTDOWN_DISCORD_INTERVALS: set[int] = {600, 300, 60}
COUNTDOWN_ALL_INTERVALS: set[int] = {600, 300, 180, 120, 60, 30, 15, 10, 5, 4, 3, 2, 1}


@dataclass
class EnginePaths:
    """Filesystem paths and service identifiers used by PalEngine."""

    ini_path: Path = field(default_factory=lambda: DEFAULT_INI_PATH)
    service_name: str = DEFAULT_SERVICE_NAME
    update_flag: Path = field(default_factory=lambda: DEFAULT_UPDATE_FLAG)
    lock_file: Path = field(default_factory=lambda: DEFAULT_LOCK_FILE)


@dataclass
class EngineConfig:
    """Encapsulates configuration settings for the PalEngine lifecycle orchestrator."""

    admin_password: str = "admin_password"
    rest_port: int = 8212
    server_name: str = "Palworld Dedicated Server"
    domain: str = "yourdomain.duckdns.org"
    discord_webhook_url: str | None = None
    paths: EnginePaths = field(default_factory=EnginePaths)


@dataclass
class CancellationControl:
    """Encapsulates cancellation signaling and administrative reason for countdowns."""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""


class PalEngine:
    """Core orchestrator for Palworld REST API, systemd operations, and reboot lifecycle.

    Attributes:
        config (EngineConfig): Engine configuration object.
        active_sockets (set[WebSocket]): Set of active client WebSocket connections.
        tracker (CommunityTracker): Telemetry and discovery hub tracker.
        notifier (DiscordNotifier): Webhook notification dispatcher.
        lifecycle_state (LifecycleState): Current active lifecycle and reboot progress state.
    """

    def __init__(self, config: EngineConfig | None = None, **kwargs: Any) -> None:
        """Initializes the PalEngine orchestrator.

        Args:
            config (EngineConfig | None): Preconfigured EngineConfig instance.
            **kwargs: Dynamic keyword arguments for backwards-compatible initialization.

        Raises:
            ValueError: If service_name contains illegal shell characters.
        """
        if config is not None:
            self.config: EngineConfig = config
        else:
            raw_svc = kwargs.get("service_name") or os.getenv("PALWORLD_SERVICE_NAME") or DEFAULT_SERVICE_NAME
            if not re.match(r"^[a-zA-Z0-9_.\-]+$", raw_svc):
                raise ValueError(f"Invalid service_name format: {raw_svc}")

            ini_p = kwargs.get("ini_path")
            up_flag = kwargs.get("update_flag")
            lk_file = kwargs.get("lock_file")

            resolved_paths = EnginePaths(
                ini_path=Path(ini_p) if ini_p else DEFAULT_INI_PATH,
                service_name=raw_svc,
                update_flag=Path(up_flag) if up_flag else DEFAULT_UPDATE_FLAG,
                lock_file=Path(lk_file) if lk_file else DEFAULT_LOCK_FILE,
            )

            self.config = EngineConfig(
                admin_password=kwargs.get("admin_password")
                or os.getenv("PALWORLD_ADMIN_PASSWORD")
                or "admin_password",
                rest_port=kwargs.get("rest_port") or int(os.getenv("PALWORLD_REST_PORT", "8212")),
                server_name=kwargs.get("server_name")
                or os.getenv("PALWORLD_SERVER_NAME")
                or "Palworld Dedicated Server",
                domain=kwargs.get("domain") or os.getenv("PALWORLD_SERVER_DOMAIN") or "yourdomain.duckdns.org",
                discord_webhook_url=kwargs.get("discord_webhook_url") or os.getenv("PALWORLD_DISCORD_WEBHOOK_URL"),
                paths=resolved_paths,
            )

        self.active_sockets: set[WebSocket] = set()
        self.tracker: CommunityTracker = CommunityTracker(self.server_name, self.domain)
        self.notifier: DiscordNotifier = DiscordNotifier(self.config.discord_webhook_url)
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._cancel_state: CancellationControl = CancellationControl()

        self.lifecycle_state: LifecycleState = {
            "phase": "IDLE",
            "remaining_seconds": 0,
            "total_seconds": 0,
            "current_broadcast": "",
            "is_updating": False,
        }

    def __getattr__(self, name: str) -> Any:
        """Delegates attribute access to self.config for backwards compatibility."""
        if hasattr(self.config, name):
            return getattr(self.config, name)
        if hasattr(self.config.paths, name):
            return getattr(self.config.paths, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    @property
    def base_url(self) -> str:
        """Base URL for local REST API endpoint."""
        return f"http://127.0.0.1:{self.config.rest_port}/v1/api"

    @property
    def auth(self) -> tuple[str, str]:
        """Basic authentication credentials for REST API."""
        return ("admin", self.config.admin_password)

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
            except WebSocketDisconnect as err:
                log.debug("WebSocket client disconnected: %s", err)
                dead_sockets.add(ws)
            except RuntimeError as err:
                log.debug("WebSocket client runtime error during send: %s", err)
                dead_sockets.add(ws)
            except OSError as err:
                log.debug("WebSocket client OS error during send: %s", err)
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

    def _check_and_acquire_lock(self) -> None:
        """Inspects and claims the atomic reboot lock file."""
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

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file.touch()

    def _set_update_flags(self) -> None:
        """Touches candidate SteamCMD update flag files across host locations."""
        candidate_flags = [
            self.update_flag,
            Path("/var/lib/palmanager/update_requested"),
            Path("/home/steam/.update_requested"),
        ]
        touched_any = False
        for flag_path in candidate_flags:
            try:
                flag_path.parent.mkdir(parents=True, exist_ok=True)
                flag_path.write_text("1\n", encoding="utf-8")
                try:
                    flag_path.chmod(0o666)
                except PermissionError as err:
                    log.debug("Could not chmod update flag %s: %s", flag_path, err)
                except OSError as err:
                    log.debug("OS error chmodding update flag %s: %s", flag_path, err)
                touched_any = True
                log.info("Set SteamCMD update flag at: %s", flag_path)
            except PermissionError as err:
                log.warning("Permission denied writing update flag at %s: %s", flag_path, err)
            except OSError as err:
                log.warning("OS error writing update flag at %s: %s", flag_path, err)
        if not touched_any:
            log.error("Could not write SteamCMD update flag to any candidate location!")

    def _clear_update_flags(self) -> None:
        """Removes candidate SteamCMD update flag files across host locations upon cancellation."""
        candidate_flags = [
            self.update_flag,
            Path("/var/lib/palmanager/update_requested"),
            Path("/home/steam/.update_requested"),
        ]
        for flag_path in candidate_flags:
            if flag_path.exists():
                try:
                    flag_path.write_text("", encoding="utf-8")
                    flag_path.unlink(missing_ok=True)
                    log.info("Cleared SteamCMD update flag at: %s", flag_path)
                except PermissionError as err:
                    log.warning("Permission denied removing update flag at %s: %s", flag_path, err)
                except OSError as err:
                    log.warning("OS error removing update flag at %s: %s", flag_path, err)

    async def _run_countdown_loop(
        self,
        countdown_seconds: int,
        trigger_update: bool,
        update_version_tag: str,
        custom_message: str,
    ) -> bool:
        """Runs the tick-by-tick countdown broadcast loop.

        Returns:
            bool: True if countdown completed naturally, False if cancelled.
        """
        remaining = countdown_seconds
        while remaining > 0:
            if self._cancel_state.event.is_set():
                log.info("Reboot countdown loop detected cancellation signal at %d seconds remaining.", remaining)
                return False

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
            try:
                await asyncio.wait_for(self._cancel_state.event.wait(), timeout=1.0)
                log.info("Reboot countdown loop awakened by cancellation event.")
                return False
            except TimeoutError:
                log.debug("Countdown tick elapsed; remaining=%d", remaining - 1)
            # In Python 3.11+, asyncio.TimeoutError is an alias for builtins.TimeoutError,
            # but in Python 3.10 they are distinct classes. Both are caught for multi-Python matrix compatibility.
            except asyncio.TimeoutError:  # pylint: disable=duplicate-except
                log.debug("Countdown tick elapsed; remaining=%d", remaining - 1)
            remaining -= 1

        return True

    async def _restart_and_await_readiness(self) -> None:
        """Saves world state, issues systemctl restart, and probes engine readiness."""
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
                cmd = [sudo_bin, "-n", systemctl_bin, "restart", self.service_name]
                proc = await asyncio.to_thread(
                    subprocess.run, cmd, capture_output=True, text=True, check=False
                )  # nosec B603
                if proc.returncode != 0:
                    log.error(
                        "systemctl restart failed with returncode %d: %s; attempting fallback binary path.",
                        proc.returncode,
                        proc.stderr.strip(),
                    )
                    fallback_systemctl = (
                        "/bin/systemctl" if systemctl_bin != "/bin/systemctl" else "/usr/bin/systemctl"
                    )
                    fallback_cmd = [sudo_bin, "-n", fallback_systemctl, "restart", self.service_name]
                    fallback_proc = await asyncio.to_thread(
                        subprocess.run, fallback_cmd, capture_output=True, text=True, check=False
                    )  # nosec B603
                    if fallback_proc.returncode != 0:
                        log.error(
                            "Fallback systemctl restart failed with returncode %d: %s",
                            fallback_proc.returncode,
                            fallback_proc.stderr.strip(),
                        )
            except OSError as err:
                log.error("Failed to execute systemctl restart: %s", err)

        # Allow systemd a moment to transition service down before probing
        await asyncio.sleep(3)

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

    def _release_reboot_lock(self) -> None:
        """Removes the atomic reboot lock file and resets lifecycle state."""
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
        self._check_and_acquire_lock()
        async with self._lifecycle_lock:
            try:
                self._cancel_state.event.clear()
                self._cancel_state.reason = ""
                if trigger_update:
                    self._set_update_flags()

                if countdown_seconds > 0:
                    self.lifecycle_state = {
                        "phase": "COUNTDOWN",
                        "remaining_seconds": countdown_seconds,
                        "total_seconds": countdown_seconds,
                        "current_broadcast": custom_message or "Initiating countdown sequence...",
                        "is_updating": trigger_update,
                    }
                    await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

                    completed = await self._run_countdown_loop(
                        countdown_seconds, trigger_update, update_version_tag, custom_message
                    )
                    if not completed:
                        log.info("Reboot countdown was cancelled by administrator. Aborting reboot procedure.")
                        cancel_msg = "Server restart CANCELLED."
                        if self._cancel_state.reason:
                            cancel_msg = f"Server restart CANCELLED: {self._cancel_state.reason}"
                        await self.send_broadcast(cancel_msg, mirror_discord=False)
                        await self.notifier.notify_reboot_cancelled(
                            server_name=self.server_name or "Palworld Dedicated Server",
                            reason=self._cancel_state.reason,
                        )
                        self._clear_update_flags()
                        return
                else:
                    # Immediate restart path (0 seconds)
                    instant_desc = (
                        "Server updating and restarting immediately."
                        if trigger_update
                        else "Server restarting immediately."
                    )
                    instant_msg = f"{custom_message} - {instant_desc}" if custom_message else instant_desc
                    await self.send_broadcast(instant_msg, mirror_discord=False)
                    await self.notifier.notify_reboot_countdown(
                        "immediately",
                        trigger_update,
                        update_version_tag,
                        custom_message=custom_message,
                    )

                await self._restart_and_await_readiness()
            finally:
                self._release_reboot_lock()
                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

    async def cancel_countdown(self, reason: str = "") -> bool:
        """Signals cancellation of an active reboot countdown sequence.

        Args:
            reason (str): Optional administrative explanation for the cancellation.

        Returns:
            bool: True if cancellation signal was successfully dispatched; False if not in COUNTDOWN phase.
        """
        if self.lifecycle_state.get("phase") != "COUNTDOWN":
            log.warning(
                "Attempted to cancel reboot while not in COUNTDOWN phase (current phase: %s).",
                self.lifecycle_state.get("phase"),
            )
            return False

        self._cancel_state.reason = reason
        self._cancel_state.event.set()
        log.info("Dispatched reboot countdown cancellation signal (reason: %r).", reason)
        return True
