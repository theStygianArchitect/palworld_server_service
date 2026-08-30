"""Palworld Operations Suite & Web Management Plane API Entrypoint.

Provides FastAPI REST endpoints, WebSocket telemetry streaming, configuration management,
and server reboot orchestration in compliance with 3 AM standards.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings, reload_settings
from .config_parser import SETTING_METADATA
from .config_pipeline import ConfigPipeline
from .engine import LOCK_FILE, PalEngine
from .git_backup import IsolatedGitBackupManager
from .logger import log
from .notifications import DiscordNotifier
from .schemas import (
    GameplaySettingsSchema,
    PlayerBanRequest,
    PlayerKickRequest,
    PlayerWarnRequest,
    RebootRequest,
)

settings = get_settings()
pipeline = ConfigPipeline(settings.ini_path)
git_mgr = IsolatedGitBackupManager(settings.ini_path)
engine = PalEngine(
    admin_password=settings.AdminPassword,
    rest_port=settings.RESTAPIPort,
    server_name=settings.ServerName,
    domain=settings.duckdns_domain,
    discord_webhook_url=settings.discord_webhook_url,
    ini_path=settings.ini_path,
    service_name=settings.service_name,
)
notifier = DiscordNotifier(settings.discord_webhook_url)


async def telemetry_streamer() -> None:
    """Continuous background loop streaming live bare-metal and server telemetry over WebSocket."""
    while True:
        try:
            liveness = False
            if os.name != "nt":
                proc = subprocess.run(
                    ["sudo", "/bin/systemctl", "is-active", settings.service_name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                liveness = proc.stdout.strip() == "active"

            readiness_data = await engine.check_readiness()
            raw_players = await engine.get_raw_players()
            player_matrix = engine.tracker.update_and_get_players(raw_players)
            metrics = await engine.get_engine_metrics()
            hw_metrics = engine.tracker.get_hardware_telemetry()
            combined_telemetry = await engine.tracker.get_combined_telemetry(
                is_multiplay=getattr(settings, "bIsMultiplay", True),
                host_ip=settings.host_ip,
                public_port=settings.PublicPort,
                server_password=settings.ServerPassword,
                rcon_port=settings.RCONPort,
                rest_port=settings.RESTAPIPort,
                max_players=metrics.get("max_players", 32),
                current_players=metrics.get("current_players", 0),
            )

            payload = {
                "type": "TELEMETRY",
                "data": {
                    "liveness": liveness,
                    "readiness": readiness_data["ready"],
                    "version": readiness_data["version"],
                    "server_fps": metrics.get("server_fps", 0),
                    "server_frame_time_ms": metrics.get("server_frame_time_ms", 0.0),
                    "uptime_seconds": metrics.get("uptime_seconds", 0),
                    "days": metrics.get("days", 0),
                    "active_players": player_matrix["active_count"],
                    "total_registered": player_matrix["total_registered"],
                    "max_players": metrics.get("max_players", 32),
                    "players_list": player_matrix["active_players"],
                    "offline_players": player_matrix["offline_players"],
                    "hardware": hw_metrics,
                    "top_badge": combined_telemetry["top_badge"],
                    "discovery_hub": combined_telemetry["discovery_hub"],
                    "a2s_telemetry": combined_telemetry["a2s_telemetry"],
                    "security_matrix": combined_telemetry["security_matrix"],
                },
            }
            await engine.broadcast_ws(payload)
        except Exception as e:
            log.warning("Telemetry stream error: %s", e)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """FastAPI application lifespan managing background loops and readiness notification."""
    log.info("Palworld Operations Suite starting on port %s", settings.web_port)
    stream_task = asyncio.create_task(telemetry_streamer())

    async def _send_startup_notice() -> None:
        await asyncio.sleep(4)
        readiness = await engine.check_readiness()
        if readiness["ready"]:
            await notifier.notify_server_ready(
                server_name=settings.ServerName or "Palworld Dedicated Server",
                version=readiness.get("version") or "Live",
                public_ip=settings.duckdns_domain,
                port=settings.PublicPort,
            )

    asyncio.create_task(_send_startup_notice())
    yield
    stream_task.cancel()
    try:
        await stream_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Palworld Operations Suite",
    description="Web Management Plane & Community Discovery Hub",
    version="1.0.0",
    lifespan=lifespan,
)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the reactive Tailwind Web management dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Palworld Operations Suite Dashboard</h2><p>Template loading...</p>")


@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """WebSocket connection point for real-time telemetry streaming."""
    await engine.register_socket(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        engine.unregister_socket(websocket)


@app.get("/api/settings")
async def get_settings_data():
    """Returns safe, public-facing server configuration and field metadata."""
    try:
        public_view = pipeline.get_public_view()
        return {"status": "success", "metadata": SETTING_METADATA, "data": public_view}
    except Exception as e:
        log.error("Error fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/settings")
async def save_sanitized_settings(payload: GameplaySettingsSchema):
    """Sanitizes, persists, and Git-commits updated gameplay settings."""
    try:
        sanitized_dict = payload.model_dump(exclude_unset=True)
        serialized_ini = pipeline.merge_and_serialize(sanitized_dict)
        Path(settings.ini_path).parent.mkdir(parents=True, exist_ok=True)
        with open(settings.ini_path, "w", encoding="utf-8") as f:
            f.write(serialized_ini)
        commit = git_mgr.create_commit("SAVE", "Web UI sanitized update")
        reload_settings()
        log.info("Saved settings cleanly to disk. Git snapshot: %s", commit)
        return {"status": "success", "message": "Settings saved cleanly to disk.", "commit": commit}
    except Exception as e:
        log.error("Save error: %s", e)
        raise HTTPException(status_code=500, detail=f"Save error: {e!s}") from e


@app.get("/api/tracker/community")
async def get_community_tracker_data():
    """Returns combined 3-section discovery hub, Steam A2S ping, and security matrix."""
    return {
        "status": "success",
        "data": await engine.tracker.get_combined_telemetry(
            host_ip=settings.host_ip,
            public_port=settings.PublicPort,
            server_password=settings.ServerPassword,
            rcon_port=settings.RCONPort,
            rest_port=settings.RESTAPIPort,
        ),
    }


@app.post("/api/service/reboot")
async def trigger_reboot(payload: RebootRequest, bg: BackgroundTasks):
    """Schedules a graceful server restart with in-game and Discord notifications."""
    if os.path.exists(LOCK_FILE):
        raise HTTPException(status_code=409, detail="Reboot sequence already in progress.")

    if payload.settings:
        serialized_ini = pipeline.merge_and_serialize(payload.settings)
        Path(settings.ini_path).parent.mkdir(parents=True, exist_ok=True)
        with open(settings.ini_path, "w", encoding="utf-8") as f:
            f.write(serialized_ini)
        commit = git_mgr.create_commit("RESTART", f"Prior to reboot ({payload.countdown_seconds}s countdown)")
        reload_settings()
        log.info("Saved configuration snapshot before reboot: %s", commit)

    log.info(
        "Initiating reboot sequence (%ss, update=%s, msg=%s)",
        payload.countdown_seconds,
        payload.trigger_steam_update,
        payload.custom_message,
    )
    bg.add_task(
        engine.execute_countdown_and_reboot,
        payload.countdown_seconds,
        payload.trigger_steam_update,
        payload.update_version_tag,
        payload.custom_message,
    )
    return {"status": "success", "message": f"Countdown sequence ({payload.countdown_seconds}s) initiated."}


@app.post("/api/players/kick")
async def handle_kick(req: PlayerKickRequest):
    """Admin endpoint to kick an online player."""
    log.info("Admin request: Kick player %s", req.player_id)
    ok = await engine.kick_player(req.player_id, req.message or "Kicked by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to kick player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} disconnected."}


@app.post("/api/players/ban")
async def handle_ban(req: PlayerBanRequest):
    """Admin endpoint to ban a player."""
    log.info("Admin request: Ban player %s", req.player_id)
    ok = await engine.ban_player(req.player_id, req.message or "Banned by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to ban player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} banned."}


@app.post("/api/players/warn")
async def handle_warn(req: PlayerWarnRequest):
    """Admin endpoint to send an announcement across in-game HUD and Discord room."""
    log.info("Admin broadcast notice: %s", req.message)
    ok = await engine.send_broadcast(f"[ADMIN NOTICE] {req.message}", mirror_discord=True)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to broadcast message.")
    return {"status": "success", "message": "Broadcast alert sent across in-game HUD and echoed to Discord."}


@app.get("/api/backups/commits")
async def get_commits():
    """Returns list of historical Git configuration snapshots."""
    return {"status": "success", "commits": git_mgr.get_history()}


@app.get("/api/backups/diff/{commit_hash}")
async def get_diff(commit_hash: str):
    """Returns unified diff between current config and a snapshot commit."""
    return {"status": "success", "diff": git_mgr.get_diff(commit_hash)}


@app.post("/api/backups/restore/{commit_hash}")
async def restore_commit(commit_hash: str):
    """Rolls back the server configuration to a historical Git commit snapshot."""
    log.info("Restoring configuration from snapshot %s", commit_hash)
    git_mgr.restore_commit(commit_hash)
    reload_settings()
    return {"status": "success", "message": f"Restored configuration from {commit_hash}."}
