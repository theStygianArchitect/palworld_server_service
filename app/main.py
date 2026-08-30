import asyncio
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import settings
from .config_parser import SETTING_METADATA
from .config_pipeline import ConfigPipeline
from .engine import LOCK_FILE, PalEngine
from .git_backup import IsolatedGitBackupManager
from .logger import log
from .notifications import DiscordNotifier
from .schemas import GameplaySettingsSchema

HTML_FILE = Path(__file__).parent / "templates" / "index.html"

pipeline = ConfigPipeline(settings.ini_path)
git_mgr = IsolatedGitBackupManager(settings.ini_path)
engine = PalEngine(
    admin_password=settings.AdminPassword,
    rest_port=settings.RESTAPIPort,
    server_name=settings.ServerName,
    domain=settings.duckdns_domain,
    discord_webhook_url=settings.discord_webhook_url,
)
notifier = DiscordNotifier(settings.discord_webhook_url)


class RebootRequest(BaseModel):
    settings: Dict[str, Any] = {}
    countdown_seconds: int = 600
    trigger_steam_update: bool = False
    update_version_tag: str = ""


class PlayerActionRequest(BaseModel):
    player_id: str
    message: str = ""


async def telemetry_streamer():
    while True:
        try:
            liveness = False
            if os.name != "nt":
                proc = subprocess.run(
                    ["sudo", "/bin/systemctl", "is-active", settings.service_name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                liveness = proc.stdout.strip() == "active"

            readiness_data = await engine.check_readiness()
            raw_players = await engine.get_raw_players()
            player_matrix = engine.tracker.update_and_get_players(raw_players)

            engine_metrics = await engine.get_engine_metrics()
            host_hardware = engine.tracker.get_hardware_telemetry()
            matchmaking_tracking = await engine.tracker.get_combined_telemetry(host_ip=settings.host_ip)

            disk_target = (
                settings.backup_dir
                if os.path.exists(settings.backup_dir)
                else ("/" if os.name != "nt" else "C:\\")
            )
            try:
                total, used, free = shutil.disk_usage(disk_target)
            except Exception:
                total, used, free = 1, 0, 1

            telemetry = {
                "type": "TELEMETRY",
                "data": {
                    "liveness": liveness,
                    "readiness": readiness_data["ready"],
                    "version": readiness_data["version"],
                    "tracking": matchmaking_tracking,
                    "players": player_matrix,
                    "metrics": {
                        "engine": engine_metrics,
                        "hardware": host_hardware,
                    },
                    "reboot_locked": os.path.exists(LOCK_FILE),
                    "disk": {
                        "total_gb": round(total / (1024**3), 1),
                        "used_gb": round(used / (1024**3), 1),
                        "free_gb": round(free / (1024**3), 1),
                        "percentage": round((used / total) * 100, 1) if total else 0,
                    },
                },
            }
            await engine.broadcast_ws(telemetry)
        except Exception as e:
            log.debug(f"Telemetry loop exception: {e}")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"Starting Palworld Operations Suite Management Plane ({settings.ServerName})")
    await engine.start_background_tracker()
    telemetry_task = asyncio.create_task(telemetry_streamer())

    # Check server initial readiness and notify discord if configured
    try:
        readiness = await engine.check_readiness()
        if readiness["ready"]:
            await notifier.notify_server_ready(
                settings.ServerName,
                readiness["version"] or "Live",
                settings.PublicIP or settings.host_ip,
                settings.PublicPort,
            )
    except Exception as e:
        log.debug(f"Startup readiness notification skipped: {e}")

    yield
    log.info("Shutting down Palworld Operations Suite Management Plane")
    telemetry_task.cancel()


app = FastAPI(title="Palworld Control Center", lifespan=lifespan)

# CORS Middleware Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws/telemetry")
async def websocket_endpoint(ws: WebSocket):
    await engine.register_socket(ws)
    try:
        await ws.send_json({"type": "LIFECYCLE_UPDATE", "data": engine.lifecycle_state})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        engine.unregister_socket(ws)


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    if not HTML_FILE.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/settings")
async def get_sanitized_settings():
    try:
        if not Path(settings.ini_path).exists():
            return {"status": "success", "metadata": SETTING_METADATA, "data": {}}
        public_view, _ = pipeline.read_to_json()
        return {"status": "success", "metadata": SETTING_METADATA, "data": public_view}
    except Exception as e:
        log.error(f"Error fetching settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings")
async def save_sanitized_settings(payload: GameplaySettingsSchema):
    try:
        sanitized_dict = payload.model_dump(exclude_unset=True)
        serialized_ini = pipeline.merge_and_serialize(sanitized_dict)
        Path(settings.ini_path).parent.mkdir(parents=True, exist_ok=True)
        with open(settings.ini_path, "w", encoding="utf-8") as f:
            f.write(serialized_ini)
        commit = git_mgr.create_commit("SAVE", "Web UI sanitized update")
        log.info(f"Saved settings cleanly to disk. Git snapshot: {commit}")
        return {"status": "success", "message": "Settings saved cleanly to disk.", "commit": commit}
    except Exception as e:
        log.error(f"Save error: {e}")
        raise HTTPException(status_code=500, detail=f"Save error: {str(e)}")


@app.post("/api/service/reboot")
async def trigger_reboot(payload: RebootRequest, bg: BackgroundTasks):
    if os.path.exists(LOCK_FILE):
        raise HTTPException(status_code=409, detail="Reboot sequence already in progress.")

    if payload.settings:
        serialized_ini = pipeline.merge_and_serialize(payload.settings)
        Path(settings.ini_path).parent.mkdir(parents=True, exist_ok=True)
        with open(settings.ini_path, "w", encoding="utf-8") as f:
            f.write(serialized_ini)
        commit = git_mgr.create_commit("RESTART", f"Prior to reboot ({payload.countdown_seconds}s countdown)")
        log.info(f"Saved configuration snapshot before reboot: {commit}")

    log.info(f"Initiating reboot sequence ({payload.countdown_seconds}s, update={payload.trigger_steam_update})")
    bg.add_task(
        engine.execute_countdown_and_reboot,
        payload.countdown_seconds,
        payload.trigger_steam_update,
        payload.update_version_tag,
    )
    return {"status": "success", "message": f"Countdown sequence ({payload.countdown_seconds}s) initiated."}


@app.post("/api/players/kick")
async def handle_kick(req: PlayerActionRequest):
    log.info(f"Admin request: Kick player {req.player_id}")
    ok = await engine.kick_player(req.player_id, req.message or "Kicked by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to kick player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} disconnected."}


@app.post("/api/players/ban")
async def handle_ban(req: PlayerActionRequest):
    log.info(f"Admin request: Ban player {req.player_id}")
    ok = await engine.ban_player(req.player_id, req.message or "Banned by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to ban player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} banned."}


@app.post("/api/players/warn")
async def handle_warn(req: PlayerActionRequest):
    log.info(f"Admin broadcast notice: {req.message}")
    ok = await engine.send_broadcast(f"[ADMIN NOTICE] {req.message}")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to broadcast message.")
    return {"status": "success", "message": "Broadcast alert sent across in-game HUD."}


@app.get("/api/backups/commits")
async def get_commits():
    return {"status": "success", "commits": git_mgr.get_history()}


@app.get("/api/backups/diff/{commit_hash}")
async def get_diff(commit_hash: str):
    return {"status": "success", "diff": git_mgr.get_diff(commit_hash)}


@app.post("/api/backups/restore/{commit_hash}")
async def restore_commit(commit_hash: str):
    log.info(f"Restoring configuration from snapshot {commit_hash}")
    git_mgr.restore_commit(commit_hash)
    return {"status": "success", "message": f"Restored configuration from {commit_hash}."}
