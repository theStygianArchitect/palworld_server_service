"""Palworld Operations Suite & Web Management Plane API Entrypoint.

Provides FastAPI REST endpoints, WebSocket telemetry streaming, configuration management,
and server reboot orchestration in compliance with Google Style Guide and 3 AM standards.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
git_mgr = IsolatedGitBackupManager(settings.ini_path, backup_repo_dir=settings.backup_repo_dir)
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
                try:
                    sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"
                    systemctl_bin = shutil.which("systemctl") or "/bin/systemctl"
                    proc = subprocess.run(
                        [sudo_bin, systemctl_bin, "is-active", settings.service_name],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    liveness = proc.stdout.strip() == "active"
                except subprocess.SubprocessError as err:
                    log.debug("Systemctl liveness probe error: %s", err)
                except OSError as err:
                    log.debug("Systemctl OS execution error: %s", err)

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

            payload: dict[str, Any] = {
                "type": "TELEMETRY",
                "data": {
                    "liveness": liveness,
                    "readiness": readiness_data["ready"],
                    "version": readiness_data["version"],
                    "server_name": settings.ServerName,
                    "server_password": settings.ServerPassword,
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
    """Manage application lifespan and background telemetry streaming loops.

    Args:
        _: Parent FastAPI application instance.

    Yields:
        None: Control back to the FastAPI runtime while application is serving.
    """
    log.info("Palworld Operations Suite starting on port %s", settings.web_port)

    # 12-Factor Resilience: Purge stale orphaned reboot lock file on startup
    if engine.lock_file.exists():
        try:
            stale_age = time.time() - engine.lock_file.stat().st_mtime
            if stale_age > 900:  # 15 minutes
                log.warning(
                    "Purging stale reboot lock file on startup (%s, age: %.1fs)",
                    engine.lock_file,
                    stale_age,
                )
                engine.lock_file.unlink(missing_ok=True)
        except PermissionError as err:
            log.debug("Permission denied checking lock file on startup: %s", err)
        except OSError as err:
            log.debug("OS error checking lock file on startup: %s", err)

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

    # Shutdown sequence
    stream_task.cancel()
    try:
        await stream_task
    except asyncio.CancelledError as err:
        log.debug("Telemetry background task cancelled during shutdown: %s", err)

    # 12-Factor Resilience: Drain and flush in-flight Discord log queue before process exit
    for handler in logging.getLogger().handlers:
        if hasattr(handler, "flush"):
            try:
                handler.flush()
            except OSError as err:
                log.debug("OS error flushing log handler during shutdown: %s", err)
            except RuntimeError as err:
                log.debug("Runtime error flushing log handler during shutdown: %s", err)


app = FastAPI(
    title="Palworld Operations Suite",
    description="Web Management Plane & Community Discovery Hub",
    version="1.0.0",
    lifespan=lifespan,
)

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Liveness probe returning daemon status and current UTC timestamp.

    Returns:
        dict[str, Any]: Liveness dictionary with status, service name, and timestamp.
    """
    return {
        "status": "healthy",
        "service": "palworld-web-manager",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@app.get("/ready")
async def readiness_check() -> dict[str, Any]:
    """Readiness probe returning Palworld game server connectivity and build version.

    Returns:
        dict[str, Any]: Readiness dictionary indicating if Palworld REST API is responding.

    Raises:
        HTTPException: 503 Service Unavailable if Palworld server is starting or unreachable.
    """
    readiness = await engine.check_readiness()
    if not readiness["ready"]:
        raise HTTPException(status_code=503, detail="Palworld server engine is starting or unreachable.")
    return {
        "status": "ready",
        "server_name": readiness["server_name"],
        "version": readiness["version"],
    }


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    """Serves the reactive Tailwind Web management dashboard.

    Returns:
        HTMLResponse: Rendered dashboard HTML content.
    """
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading template file at %s: %s", template_path, err)
    return HTMLResponse("<h2>Palworld Operations Suite Dashboard</h2><p>Template loading...</p>")


@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Websocket connection endpoint for real-time telemetry streaming.

    Args:
        websocket (WebSocket): Inbound client WebSocket connection.
    """
    await engine.register_socket(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect as err:
        log.debug("WebSocket client disconnected: %s", err)
        engine.unregister_socket(websocket)


@app.get("/api/settings")
async def get_settings_data() -> dict[str, Any]:
    """Returns safe, public-facing server configuration and field metadata.

    Returns:
        dict[str, Any]: Mapping with status, field metadata, and public settings dictionary.

    Raises:
        HTTPException: If reading configuration fails.
    """
    try:
        public_view = pipeline.get_public_view()
        return {"status": "success", "metadata": SETTING_METADATA, "data": public_view}
    except Exception as e:
        log.error("Error fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/settings")
async def save_sanitized_settings(payload: GameplaySettingsSchema) -> dict[str, Any]:
    """Sanitizes, persists, and Git-commits updated gameplay settings.

    Args:
        payload (GameplaySettingsSchema): Validated gameplay settings input.

    Returns:
        dict[str, Any]: Success status, message, and Git snapshot commit hash.

    Raises:
        HTTPException: If persisting settings fails.
    """
    try:
        sanitized_dict = payload.model_dump(exclude_unset=True)
        serialized_ini = pipeline.merge_and_serialize(sanitized_dict)
        ini_file = Path(settings.ini_path)
        try:
            ini_file.parent.mkdir(parents=True, exist_ok=True)
            ini_file.write_text(serialized_ini, encoding="utf-8")
        except PermissionError as err:
            log.warning("Permission denied writing INI at %s: %s. Using home directory fallback.", ini_file, err)
            ini_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
            ini_file.parent.mkdir(parents=True, exist_ok=True)
            ini_file.write_text(serialized_ini, encoding="utf-8")
        except OSError as err:
            log.warning("OS error writing INI at %s: %s. Using home directory fallback.", ini_file, err)
            ini_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
            ini_file.parent.mkdir(parents=True, exist_ok=True)
            ini_file.write_text(serialized_ini, encoding="utf-8")
        commit = git_mgr.create_commit("SAVE", "Web UI sanitized update")
        reload_settings()
        log.info("Saved settings cleanly to disk. Git snapshot: %s", commit)
        return {"status": "success", "message": "Settings saved cleanly to disk.", "commit": commit}
    except Exception as e:
        log.error("Save error: %s", e)
        raise HTTPException(status_code=500, detail=f"Save error: {e!s}") from e


@app.get("/api/tracker/community")
async def get_community_tracker_data() -> dict[str, Any]:
    """Returns combined 3-section discovery hub, Steam A2S ping, and security matrix.

    Returns:
        dict[str, Any]: Combined discovery, A2S telemetry, and security matrix payload.
    """
    data = await engine.tracker.get_combined_telemetry(
        host_ip=settings.host_ip,
        public_port=settings.PublicPort,
        server_password=settings.ServerPassword,
        rcon_port=settings.RCONPort,
        rest_port=settings.RESTAPIPort,
    )
    return {"status": "success", "data": data}


@app.post("/api/service/reboot")
async def trigger_reboot(payload: RebootRequest, bg: BackgroundTasks) -> dict[str, Any]:
    """Schedules a graceful server restart with in-game and Discord notifications.

    Args:
        payload (RebootRequest): Reboot configuration including countdown and custom message.
        bg (BackgroundTasks): FastAPI background task manager.

    Returns:
        dict[str, Any]: Success response acknowledging countdown initiation.

    Raises:
        HTTPException: If another reboot sequence is already in progress.
    """
    if LOCK_FILE.exists():
        raise HTTPException(status_code=409, detail="Reboot sequence already in progress.")

    if payload.settings:
        serialized_ini = pipeline.merge_and_serialize(payload.settings)
        ini_file = Path(settings.ini_path)
        try:
            ini_file.parent.mkdir(parents=True, exist_ok=True)
            ini_file.write_text(serialized_ini, encoding="utf-8")
        except PermissionError as err:
            log.warning("Permission denied writing INI at %s: %s. Using home directory fallback.", ini_file, err)
            ini_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
            ini_file.parent.mkdir(parents=True, exist_ok=True)
            ini_file.write_text(serialized_ini, encoding="utf-8")
        except OSError as err:
            log.warning("OS error writing INI at %s: %s. Using home directory fallback.", ini_file, err)
            ini_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
            ini_file.parent.mkdir(parents=True, exist_ok=True)
            ini_file.write_text(serialized_ini, encoding="utf-8")
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
async def handle_kick(req: PlayerKickRequest) -> dict[str, Any]:
    """Admin endpoint to kick an online player.

    Args:
        req (PlayerKickRequest): Target player ID and moderation reason.

    Returns:
        dict[str, Any]: Success response.

    Raises:
        HTTPException: If the in-engine kick command fails.
    """
    log.info("Admin request: Kick player %s", req.player_id)
    ok = await engine.kick_player(req.player_id, req.message or "Kicked by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to kick player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} disconnected."}


@app.post("/api/players/ban")
async def handle_ban(req: PlayerBanRequest) -> dict[str, Any]:
    """Admin endpoint to ban a player.

    Args:
        req (PlayerBanRequest): Target player ID and moderation reason.

    Returns:
        dict[str, Any]: Success response.

    Raises:
        HTTPException: If the in-engine ban command fails.
    """
    log.info("Admin request: Ban player %s", req.player_id)
    ok = await engine.ban_player(req.player_id, req.message or "Banned by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to ban player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} banned."}


@app.post("/api/players/warn")
async def handle_warn(req: PlayerWarnRequest) -> dict[str, Any]:
    """Admin endpoint to send an announcement across in-game HUD and Discord room.

    Args:
        req (PlayerWarnRequest): Broadcast announcement message string.

    Returns:
        dict[str, Any]: Success response.

    Raises:
        HTTPException: If broadcasting the announcement fails.
    """
    log.info("Admin broadcast notice: %s", req.message)
    ok = await engine.send_broadcast(f"[ADMIN NOTICE] {req.message}", mirror_discord=True)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to broadcast message.")
    return {"status": "success", "message": "Broadcast alert sent across in-game HUD and echoed to Discord."}


@app.get("/api/backups/commits")
async def get_commits() -> dict[str, Any]:
    """Returns list of historical Git configuration snapshots.

    Returns:
        dict[str, Any]: Dictionary containing list of snapshot commit summaries.
    """
    return {"status": "success", "commits": git_mgr.get_history()}


@app.get("/api/backups/diff/{commit_hash}")
async def get_diff(commit_hash: str) -> dict[str, Any]:
    """Returns unified diff between current config and a snapshot commit.

    Args:
        commit_hash (str): Target Git snapshot commit hash.

    Returns:
        dict[str, Any]: Dictionary containing unified diff text.
    """
    if not re.match(r"^[a-fA-F0-9]{4,64}$", commit_hash):
        raise HTTPException(status_code=400, detail="Invalid Git commit hash format.")
    return {"status": "success", "diff": git_mgr.get_diff(commit_hash)}


@app.post("/api/backups/restore/{commit_hash}")
async def restore_commit(commit_hash: str) -> dict[str, Any]:
    """Rolls back the server configuration to a historical Git commit snapshot.

    Args:
        commit_hash (str): Target Git snapshot commit hash to restore.

    Returns:
        dict[str, Any]: Success response.
    """
    if not re.match(r"^[a-fA-F0-9]{4,64}$", commit_hash):
        raise HTTPException(status_code=400, detail="Invalid Git commit hash format.")
    log.info("Restoring configuration from snapshot %s", commit_hash)
    success = git_mgr.restore_commit(commit_hash)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to restore commit {commit_hash}.")
    reload_settings()
    return {"status": "success", "message": f"Restored configuration from {commit_hash}."}
