import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from fastapi import WebSocket
import httpx
from .logger import log
from .notifications import DiscordNotifier
from .tracker import CommunityTracker

INI_PATH = os.getenv(
    "PALWORLD_INI_PATH",
    "/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
    if os.name != "nt"
    else os.path.expanduser("~/.palmanager/PalWorldSettings.ini"),
)
SERVICE_NAME = os.getenv("PALWORLD_SERVICE_NAME", "palworld.service")
UPDATE_FLAG = os.getenv(
    "PALWORLD_UPDATE_FLAG",
    "/home/steam/.update_requested" if os.name != "nt" else os.path.expanduser("~/.palmanager/.update_requested"),
)
import tempfile

LOCK_FILE = os.getenv(
    "PALWORLD_LOCK_FILE",
    os.path.join(tempfile.gettempdir(), "palworld_reboot.lock"),
)


class PalEngine:
    def __init__(
        self,
        admin_password: Optional[str] = None,
        rest_port: Optional[int] = None,
        server_name: Optional[str] = None,
        domain: Optional[str] = None,
        discord_webhook_url: Optional[str] = None,
    ):
        self.admin_password = admin_password or os.getenv("PALWORLD_ADMIN_PASSWORD", "admin_password")
        self.rest_port = rest_port or int(os.getenv("PALWORLD_REST_PORT", "8212"))
        self.server_name = server_name or os.getenv("PALWORLD_SERVER_NAME", "Palworld Dedicated Server")
        self.domain = domain or os.getenv("PALWORLD_SERVER_DOMAIN", "yourdomain.duckdns.org")

        self.base_url = f"http://127.0.0.1:{self.rest_port}/v1/api"
        self.auth = ("admin", self.admin_password)
        self.active_sockets: Set[WebSocket] = set()
        self.tracker = CommunityTracker(self.server_name, self.domain)
        self.notifier = DiscordNotifier(discord_webhook_url or os.getenv("PALWORLD_DISCORD_WEBHOOK_URL"))

        self.lifecycle_state = {
            "phase": "IDLE",
            "remaining_seconds": 0,
            "total_seconds": 0,
            "current_broadcast": "",
            "is_updating": False,
        }

    async def start_background_tracker(self):
        asyncio.create_task(self.tracker.probe_battlemetrics_loop())

    async def register_socket(self, ws: WebSocket):
        await ws.accept()
        self.active_sockets.add(ws)

    def unregister_socket(self, ws: WebSocket):
        self.active_sockets.discard(ws)

    async def broadcast_ws(self, payload: Dict[str, Any]):
        for ws in list(self.active_sockets):
            try:
                await ws.send_json(payload)
            except Exception:
                self.active_sockets.discard(ws)

    async def send_broadcast(self, message: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(
                    f"{self.base_url}/announce",
                    auth=self.auth,
                    json={"message": message},
                )
                return res.status_code == 200
        except Exception as e:
            log.debug(f"Broadcast notice failed: {e}")
            return False

    async def kick_player(self, player_id: str, message: str = "Kicked by administrator") -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.post(
                    f"{self.base_url}/kick",
                    auth=self.auth,
                    json={"userid": player_id, "message": message},
                )
                if res.status_code == 200:
                    await self.notifier.notify_player_action("kick", player_id, message)
                    return True
        except Exception as e:
            log.warning(f"Kick player {player_id} failed: {e}")
        return False

    async def ban_player(self, player_id: str, message: str = "Banned by administrator") -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.post(
                    f"{self.base_url}/ban",
                    auth=self.auth,
                    json={"userid": player_id, "message": message},
                )
                if res.status_code == 200:
                    await self.notifier.notify_player_action("ban", player_id, message)
                    return True
        except Exception as e:
            log.warning(f"Ban player {player_id} failed: {e}")
        return False

    async def unban_player(self, player_id: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.post(
                    f"{self.base_url}/unban",
                    auth=self.auth,
                    json={"userid": player_id},
                )
                return res.status_code == 200
        except Exception as e:
            log.warning(f"Unban player {player_id} failed: {e}")
            return False

    async def trigger_save(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.post(f"{self.base_url}/save", auth=self.auth)
                return res.status_code == 200
        except Exception as e:
            log.warning(f"World save failed: {e}")
            return False

    async def get_raw_players(self) -> List[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(f"{self.base_url}/players", auth=self.auth)
                if res.status_code == 200:
                    return res.json().get("players", [])
        except Exception:
            pass
        return []

    async def get_engine_metrics(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(f"{self.base_url}/metrics", auth=self.auth)
                if res.status_code == 200:
                    d = res.json()
                    return {
                        "server_fps": round(float(d.get("serverfps", 0)), 1),
                        "server_frame_time_ms": round(float(d.get("serverframetime", 0)), 2),
                        "current_players": d.get("currentplayernum", 0),
                        "max_players": d.get("maxplayernum", 32),
                        "uptime_sec": d.get("uptime", 0),
                        "days": d.get("days", 0),
                    }
        except Exception:
            pass
        return {
            "server_fps": 0,
            "server_frame_time_ms": 0,
            "current_players": 0,
            "max_players": 32,
            "uptime_sec": 0,
            "days": 0,
        }

    async def check_readiness(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(f"{self.base_url}/info", auth=self.auth)
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "ready": True,
                        "version": data.get("version", "Live"),
                        "server_name": data.get("servername", ""),
                    }
        except Exception:
            pass
        return {"ready": False, "version": None, "server_name": None}

    async def execute_countdown_and_reboot(
        self,
        countdown_seconds: int = 600,
        trigger_update: bool = False,
        update_version_tag: str = "",
    ):
        if os.path.exists(LOCK_FILE):
            raise RuntimeError("Reboot countdown sequence is already active.")

        Path(LOCK_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(LOCK_FILE).touch()
        try:
            if trigger_update:
                Path(UPDATE_FLAG).parent.mkdir(parents=True, exist_ok=True)
                Path(UPDATE_FLAG).touch()

            self.lifecycle_state = {
                "phase": "COUNTDOWN",
                "remaining_seconds": countdown_seconds,
                "total_seconds": countdown_seconds,
                "current_broadcast": "Initiating countdown sequence...",
                "is_updating": trigger_update,
            }
            await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

            intervals = {600, 300, 180, 120, 60, 30, 15, 10, 5, 4, 3, 2, 1}
            discord_intervals = {600, 300, 60}  # 10m, 5m, 1m warnings to Discord
            remaining = countdown_seconds

            while remaining > 0:
                self.lifecycle_state["remaining_seconds"] = remaining
                if remaining in intervals or remaining == countdown_seconds:
                    if remaining >= 60:
                        mins = remaining // 60
                        time_str = f"{mins} minute{'s' if mins > 1 else ''}"
                    else:
                        time_str = f"{remaining} seconds"

                    msg = f"Server maintenance restart in {time_str}."
                    if trigger_update:
                        target_info = f" to {update_version_tag}" if update_version_tag else ""
                        msg = f"Server updating{target_info} and restarting in {time_str}."

                    self.lifecycle_state["current_broadcast"] = msg
                    await self.send_broadcast(msg)

                    if remaining in discord_intervals or remaining == countdown_seconds:
                        await self.notifier.notify_reboot_countdown(time_str, trigger_update, update_version_tag)

                await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})
                await asyncio.sleep(1)
                remaining -= 1

            self.lifecycle_state["phase"] = "SAVING"
            self.lifecycle_state["current_broadcast"] = "Saving world state to disk..."
            await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})
            await self.send_broadcast("Server restarting NOW. Saving progress.")
            await self.trigger_save()
            await asyncio.sleep(1)

            self.lifecycle_state["phase"] = "MAINTENANCE"
            self.lifecycle_state["current_broadcast"] = "Executing systemctl restart & backup hooks..."
            await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

            if os.name != "nt":
                log.info(f"Triggering systemctl restart for {SERVICE_NAME}")
                subprocess.run(["sudo", "/bin/systemctl", "restart", SERVICE_NAME], check=False)

            self.lifecycle_state["phase"] = "PROBING"
            self.lifecycle_state["current_broadcast"] = "Probing engine initialization and readiness..."
            await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})

            for _ in range(90):
                ready_check = await self.check_readiness()
                if ready_check["ready"]:
                    log.info("Server engine restored to ready state.")
                    await self.notifier.notify_reboot_complete(self.server_name)
                    break
                await asyncio.sleep(2)

        finally:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
            self.lifecycle_state = {
                "phase": "IDLE",
                "remaining_seconds": 0,
                "total_seconds": 0,
                "current_broadcast": "",
                "is_updating": False,
            }
            await self.broadcast_ws({"type": "LIFECYCLE_UPDATE", "data": self.lifecycle_state})
