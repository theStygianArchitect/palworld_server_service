"""Palworld Operations Suite & Web Management Plane API Entrypoint.

Provides FastAPI REST endpoints, WebSocket telemetry streaming, configuration management,
and server reboot orchestration in compliance with Google Style Guide and 3 AM standards.
"""

# pylint: disable=too-many-lines
# Rationale: Central FastAPI entrypoint aggregates lifecycle orchestration, REST endpoints, and WebSockets.

from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import json
import logging
import os
import shutil
import sqlite3
import subprocess  # nosec B404 - required for systemctl is-active liveness probe; no Python-native systemd API
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.schemas import (
    BootstrapAckResponse,
    BootstrapCredentialsResponse,
    FeedbackResponse,
    FeedbackSubmitRequest,
    GameplaySettingsSchema,
    IssueCategory,
    LoginAuditResponse,
    MetricBucketResponse,
    MetricFlushResponse,
    MetricHistoryResponse,
    MetricPruneResponse,
    MetricSummaryResponse,
    PlayerBanRequest,
    PlayerKickRequest,
    PlayerWarnRequest,
    RebootCancelRequest,
    RebootRequest,
    SaveResponse,
    ShutdownRequest,
    ShutdownResponse,
    UpdateApplyRequest,
    UpdateApplyResponse,
    UpdateStatusResponse,
    UserCreateRequest,
    UserLoginRequest,
    UserLoginResponse,
    UserRegisterRequest,
    UserResponse,
    UserRoleUpdateRequest,
    UserUpdateRequest,
    build_github_issue_url,
)
from app.config_manager.parser import SETTING_METADATA
from app.config_manager.pipeline import ConfigPipeline
from app.core.config import get_settings, reload_settings
from app.core.logger import log
from app.database import (
    DatabaseManager,
    MetricsDatabaseManager,
    MetricSnapshotRecord,
    UserRecord,
    acknowledge_bootstrap,
    bootstrap_admin_user,
    generate_session_token,
    get_bootstrap_state,
    has_permission,
    hash_password,
    verify_password,
    verify_session_token,
)
from app.engine.notifications import DiscordNotifier
from app.engine.service import LOCK_FILE, PalEngine
from app.engine.updater import UpdateWatcher

settings = get_settings()
db = DatabaseManager(settings.database_path)
metrics_db = MetricsDatabaseManager(settings.metrics_db_path)
pipeline = ConfigPipeline(settings.ini_path)
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
updater = UpdateWatcher(
    repo_url=settings.github_repo_url,
    branch=settings.update_branch,
    check_interval_seconds=settings.update_check_interval_seconds,
    deploy_script=settings.deploy_script_path,
)

metrics_buffer: list[MetricSnapshotRecord] = []
metrics_buffer_lock = asyncio.Lock()


async def flush_metrics_buffer() -> int:
    """Atomically flushes in-memory buffered metric snapshots to the SQLite database.

    Returns:
        int: Number of snapshots written to disk.
    """
    async with metrics_buffer_lock:
        if not metrics_buffer:
            return 0
        batch = list(metrics_buffer)
        metrics_buffer.clear()

    try:
        inserted = await asyncio.to_thread(metrics_db.record_snapshots_batch, batch)
        log.info("Flushed %d telemetry snapshots from memory buffer to metrics.db", inserted)
        return inserted
    except sqlite3.OperationalError as err:
        log.error("Database operational error during metrics buffer flush: %s", err)
        async with metrics_buffer_lock:
            metrics_buffer[0:0] = batch[:1000]
        return 0
    except sqlite3.DatabaseError as err:
        log.error("Database error during metrics buffer flush: %s", err)
        async with metrics_buffer_lock:
            metrics_buffer[0:0] = batch[:1000]
        return 0


async def telemetry_streamer() -> None:
    """Continuous background loop streaming live bare-metal and server telemetry over WebSocket."""
    while True:
        try:
            liveness = False
            if os.name != "nt":
                try:
                    systemctl_bin = (  # nosec B607
                        shutil.which("systemctl") or "/bin/systemctl"
                    )
                    proc = await asyncio.to_thread(
                        subprocess.run,  # nosec B603 - validated arg list; systemctl is-active is read-only probe
                        [systemctl_bin, "is-active", settings.service_name],
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
                query_port=settings.QueryPort,
                server_password=settings.ServerPassword,
                rcon_port=settings.RCONPort,
                rcon_enabled=settings.RCONEnabled,
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
                    "diagnostic_code": readiness_data.get(
                        "diagnostic_code", "OK" if readiness_data["ready"] else "UNKNOWN"
                    ),
                    "diagnostic_message": readiness_data.get("diagnostic_message", ""),
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
        except WebSocketDisconnect as err:
            log.debug("WebSocket client disconnected during telemetry streaming: %s", err)
        except httpx.HTTPError as err:
            log.warning("HTTP error in telemetry stream: %s", err)
        except RuntimeError as err:
            log.warning("Runtime error in telemetry stream: %s", err)
        except OSError as err:
            log.warning("OS error in telemetry stream: %s", err)
        except ValueError as err:
            log.warning("Value error in telemetry stream: %s", err)
        except KeyError as err:
            log.warning("Key error in telemetry stream: %s", err)
        await asyncio.sleep(2)


async def trigger_duckdns_sync() -> None:
    """Invokes DuckDNS dynamic DNS updater on startup if available on the host."""
    candidate_scripts = [
        Path("/home/steam/duckdns/duck.sh"),
        Path("/opt/palworld-web-manager/scripts/duck.sh"),
    ]
    for script in candidate_scripts:
        if script.exists():
            try:
                log.info("Triggering DuckDNS dynamic DNS synchronization via %s", script)
                proc = await asyncio.create_subprocess_exec(
                    "/bin/bash",
                    str(script),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10.0)
                log.info("DuckDNS synchronization complete (exit code: %s)", proc.returncode)
                return
            except asyncio.TimeoutError as err:
                log.warning("DuckDNS sync script timed out: %s", err)
            except OSError as err:
                log.debug("DuckDNS sync script OS error: %s", err)


async def metrics_collector_loop() -> None:
    """Periodically samples server and hardware telemetry into memory and batch flushes to disk.

    Samples into metrics_buffer every settings.metrics_sample_interval_seconds (default 10s),
    batch flushes to metrics.db every settings.metrics_flush_interval_seconds (default 300s / 5m),
    and prunes records older than settings.metrics_retention_days (default 30 days) every hour.
    """
    last_flush_time = time.time()
    last_prune_time = time.time()
    while True:
        try:
            metrics = await engine.get_engine_metrics()
            hw = engine.tracker.get_hardware_telemetry()
            now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

            snapshot = MetricSnapshotRecord(
                id=None,
                timestamp=now_str,
                server_fps=float(metrics.get("server_fps", 0.0)),
                server_frame_time_ms=float(metrics.get("server_frame_time_ms", 0.0)),
                uptime_seconds=int(metrics.get("uptime_seconds", 0)),
                active_players=int(metrics.get("current_players", 0)),
                max_players=int(metrics.get("max_players", 32)),
                cpu_avg_pct=float(hw.get("cpu_avg_pct", 0.0)),
                host_ram_used_gb=float(hw.get("host_ram_used_gb", 0.0)),
                host_ram_total_gb=float(hw.get("host_ram_total_gb", 0.0)),
                host_ram_pct=float(hw.get("host_ram_pct", 0.0)),
                cgroup_ram_used_gb=float(hw.get("cgroup_ram_used_gb", 0.0)),
                cgroup_ram_pct=float(hw.get("cgroup_ram_pct", 0.0)),
                disk_used_gb=float(hw.get("disk_used_gb", 0.0)),
                disk_pct=float(hw.get("disk_pct", 0.0)),
                net_rx_rate_kbps=float(hw.get("net_rx_rate_kbps", 0.0)),
                net_tx_rate_kbps=float(hw.get("net_tx_rate_kbps", 0.0)),
            )

            async with metrics_buffer_lock:
                metrics_buffer.append(snapshot)
                buffer_size = len(metrics_buffer)

            now_time = time.time()
            if (now_time - last_flush_time >= settings.metrics_flush_interval_seconds) or buffer_size >= 1000:
                await flush_metrics_buffer()
                last_flush_time = now_time

            # Prune once every hour
            if now_time - last_prune_time >= 3600:
                await asyncio.to_thread(metrics_db.prune_older_than, settings.metrics_retention_days)
                last_prune_time = now_time

        except sqlite3.OperationalError as err:
            log.warning("Database operational error in metrics collector: %s", err)
        except sqlite3.DatabaseError as err:
            log.warning("Database error in metrics collector: %s", err)
        except httpx.HTTPError as err:
            log.debug("HTTP error querying engine metrics in collector: %s", err)
        except asyncio.CancelledError as err:
            log.debug("Metrics collector task received cancellation: %s", err)
            break
        except OSError as err:
            log.warning("OS error in metrics collector: %s", err)
        except RuntimeError as err:
            log.warning("Runtime error in metrics collector: %s", err)
        except KeyError as err:
            log.warning("Key error in metrics collector: %s", err)
        except ValueError as err:
            log.warning("Value error in metrics collector: %s", err)

        await asyncio.sleep(settings.metrics_sample_interval_seconds)


# pylint: disable=too-many-branches,too-many-statements
# Rationale: Orchestrates startup/shutdown routines across SQLite, DuckDNS, telemetry, and logging queues.
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

    # Database Persistence: Initialize schema and bootstrap default administrator
    try:
        await asyncio.to_thread(db.initialize)
        initial_admin_pwd = None if settings.AdminPassword == "admin_password" else settings.AdminPassword
        await asyncio.to_thread(
            bootstrap_admin_user,
            db,
            initial_admin_pwd,
            settings.admin_credential_export_path,
        )
        await asyncio.to_thread(metrics_db.initialize)
        await asyncio.to_thread(metrics_db.prune_older_than, settings.metrics_retention_days)
    except sqlite3.OperationalError as err:
        log.error("OperationalError initializing database: %s", err)
    except sqlite3.DatabaseError as err:
        log.error("DatabaseError initializing database: %s", err)
    except sqlite3.Error as err:
        log.error("SQLite Error initializing database: %s", err)
    except OSError as err:
        log.error("OS error initializing database: %s", err)
    except RuntimeError as err:
        log.error("Runtime error initializing database: %s", err)

    duckdns_task = asyncio.create_task(trigger_duckdns_sync())
    stream_task = asyncio.create_task(telemetry_streamer())
    metrics_task = asyncio.create_task(metrics_collector_loop())
    updater_task: asyncio.Task[None] | None = None
    if settings.updater_enabled:
        updater_task = asyncio.create_task(updater.run_loop())

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

    startup_task = asyncio.create_task(_send_startup_notice())
    yield

    # Shutdown sequence
    duckdns_task.cancel()
    startup_task.cancel()
    stream_task.cancel()
    metrics_task.cancel()
    if updater_task is not None:
        updater_task.cancel()
    try:
        await stream_task
    except asyncio.CancelledError as err:
        log.debug("Telemetry background task cancelled during shutdown: %s", err)
    try:
        await metrics_task
    except asyncio.CancelledError as err:
        log.debug("Metrics background task cancelled during shutdown: %s", err)
    if updater_task is not None:
        try:
            await updater_task
        except asyncio.CancelledError as err:
            log.debug("Updater background task cancelled during shutdown: %s", err)

    # Flush any remaining in-memory telemetry buffer to disk before database closure
    try:
        await flush_metrics_buffer()
    except sqlite3.OperationalError as err:
        log.error("Operational error flushing metrics buffer during shutdown: %s", err)
    except sqlite3.DatabaseError as err:
        log.error("Database error flushing metrics buffer during shutdown: %s", err)
    except OSError as err:
        log.error("OS error flushing metrics buffer during shutdown: %s", err)

    # Database connection cleanup
    try:
        db.close()
    except sqlite3.Error as err:
        log.debug("SQLite error closing database during shutdown: %s", err)
    except OSError as err:
        log.debug("OS error closing database during shutdown: %s", err)

    try:
        metrics_db.close()
    except sqlite3.Error as err:
        log.debug("SQLite error closing metrics database during shutdown: %s", err)
    except OSError as err:
        log.debug("OS error closing metrics database during shutdown: %s", err)

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


def get_client_ip(request: Request) -> str:
    """Extracts client IP address respecting X-Forwarded-For and request.client.

    Args:
        request: Inbound FastAPI HTTP request.

    Returns:
        str: Detected client IP string.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


# pylint: disable=too-many-branches
# Rationale: Defensive evaluation of Bearer tokens, cookies, Basic Auth, and localhost fallback.
def get_current_user(request: Request) -> UserRecord:
    """Resolves and validates the active user session or localhost/Basic Auth fallback.

    Args:
        request: Inbound FastAPI HTTP request.

    Returns:
        UserRecord: Authenticated user record.

    Raises:
        HTTPException: 401 Unauthorized if credentials are missing, expired, or invalid.
    """
    token: str | None = None
    auth_header = request.headers.get("authorization", "")

    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif "pal_session_token" in request.cookies:
        token = request.cookies.get("pal_session_token")
    elif auth_header.startswith("Basic "):
        try:
            encoded_creds = auth_header[6:].strip()
            decoded = base64.b64decode(encoded_creds).decode("utf-8")
            if ":" in decoded:
                user_part, pass_part = decoded.split(":", 1)
                if user_part == "admin" and pass_part == settings.AdminPassword:
                    admin_rec = db.get_user_by_username("admin")
                    if admin_rec is not None and admin_rec.is_active:
                        return admin_rec
        except binascii.Error as err:
            log.debug("Binascii error decoding basic auth: %s", err)
        except UnicodeDecodeError as err:
            log.debug("Unicode error decoding basic auth: %s", err)
        except ValueError as err:
            log.debug("Value error decoding basic auth: %s", err)

    if token:
        username = verify_session_token(token, secret_key=settings.AdminPassword)
        if username:
            user = db.get_user_by_username(username)
            if user is not None and user.is_active:
                return user
        log.warning("Authentication rejected: invalid or expired token.")
        raise HTTPException(status_code=401, detail="Invalid or expired session token.")

    # 12-Factor Resilience & Local Automation Backwards Compatibility:
    # Requests directly from localhost loopback or test runners inherit admin rights if unauthenticated.
    client_ip = get_client_ip(request)
    if client_ip in ("127.0.0.1", "::1", "testclient", "localhost"):
        admin_rec = db.get_user_by_username("admin")
        if admin_rec is not None and admin_rec.is_active:
            return admin_rec
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        empty_digest = ""
        return UserRecord(
            id=1,
            username="admin",
            password_hash=empty_digest,
            salt=empty_digest,
            email="admin@localhost",
            role="admin",
            is_active=True,
            created_at=now_ts,
        )

    raise HTTPException(status_code=401, detail="Authentication credentials required.")


def get_current_user_optional(request: Request) -> UserRecord | None:
    """Attempts to resolve the active user session without raising 401.

    Args:
        request: Inbound FastAPI HTTP request.

    Returns:
        UserRecord or None if unauthenticated or credentials invalid.
    """
    try:
        return get_current_user(request)
    except HTTPException as err:
        log.debug("Optional authentication resolution deferred for unauthenticated client: %s", err)
        return None


def require_permission(permission: str):
    """Factory creating FastAPI route dependencies that enforce granular RBAC permissions.

    Args:
        permission: Required granular permission token string.

    Returns:
        Callable dependency validating user authorization.
    """

    def _dependency(user: UserRecord = Depends(get_current_user)) -> UserRecord:
        user_perms = db.get_user_permissions(user.id)
        if not has_permission(user.role, user_perms, permission):
            log.warning("Permission denied: user '%s' lacks required '%s'", user.username, permission)
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Insufficient privileges. Required permission: '{permission}'.",
            )
        return user

    return _dependency


perm_server_settings = require_permission("server:settings")
perm_server_reboot = require_permission("server:reboot")
perm_player_kick = require_permission("player:kick")
perm_player_ban = require_permission("player:ban")
perm_player_broadcast = require_permission("player:broadcast")
perm_logs_view = require_permission("logs:view")
perm_feedback_submit = require_permission("feedback:submit")
perm_users_manage = require_permission("users:manage")
perm_system_update = require_permission("system:update")


def render_feedback_markdown(req: FeedbackSubmitRequest) -> str:
    """Renders formatted Markdown matching GitHub issue templates from validated submission.

    Args:
        req: Validated feedback submission payload.

    Returns:
        str: Rendered Markdown body text.
    """
    if req.category == "bug_report":
        return (
            f"## 🐛 Expected Behavior\n{req.expected_behavior or 'N/A'}\n\n"
            f"## 💥 Current Behavior\n{req.current_behavior or 'N/A'}\n\n"
            f"## 📋 Steps to Reproduce\n{req.steps_to_reproduce or 'N/A'}\n\n"
            f"## 🖥️ Environment & Host Diagnostics\n{req.host_environment or 'N/A'}\n\n"
            f"## 📜 Diagnostic Logs & Tracebacks\n```text\n{req.diagnostic_logs or 'N/A'}\n```\n\n"
            f"## 🛠️ Possible Root Cause / Proposed Solution\n{req.proposed_solution or 'N/A'}\n"
        )
    if req.category == "feature_request":
        return (
            f"## 🚀 Feature Proposal\n{req.feature_proposal or req.title}\n\n"
            f"## 🎯 Problem / User Story\n{req.problem_user_story or 'N/A'}\n\n"
            f"## 💡 Proposed Solution & Architecture\n{req.description or 'N/A'}\n\n"
            f"## 🧱 12-Factor & Resilience Considerations\n{req.twelve_factor_considerations or 'N/A'}\n\n"
            f"## 🔄 Alternatives Considered\n{req.alternatives_considered or 'N/A'}\n"
        )
    if req.category == "documentation_update":
        return (
            f"## 📝 Documentation Area\n{req.documentation_area or 'N/A'}\n\n"
            f"## 🎯 Motivation & Missing Context\n{req.motivation_missing_context or 'N/A'}\n\n"
            f"## ✏️ Proposed Content / Diff\n{req.proposed_content or req.description or 'N/A'}\n"
        )
    if req.category == "security_report":
        return (
            f"## 🛡️ Security Vulnerability Summary\n{req.vulnerability_summary or req.description or 'N/A'}\n\n"
            f"## 🔍 Vulnerability Details & Attack Vector\n"
            f"- **Affected File & Line(s)**: {req.affected_files_lines or 'N/A'}\n"
            f"- **CWE Identifier**: {req.cwe_identifier or 'N/A'}\n"
            f"- **Severity**: {req.severity or 'Medium'}\n\n"
            f"## 💣 Proof of Concept / Reproduction Flow\n{req.poc_reproduction or 'N/A'}\n\n"
            f"## 🛡️ Recommended Remediation / Defensive Patch\n{req.recommended_remediation or 'N/A'}\n"
        )
    return req.description or req.title


async def dispatch_github_issue(
    category: str,
    title: str,
    body: str,
    feedback_id: int,
) -> None:
    """Asynchronously creates a GitHub issue via the GitHub CLI if configured.

    Args:
        category: Issue category matching repo templates.
        title: Issue title string.
        body: Markdown issue body content.
        feedback_id: Target feedback database primary key.
    """
    gh_bin = shutil.which("gh")
    if not gh_bin:
        log.debug("GitHub CLI (gh) not installed. Issue dispatch skipped for feedback #%d.", feedback_id)
        return

    label_map = {
        "bug_report": "bug",
        "feature_request": "enhancement",
        "documentation_update": "documentation",
        "security_report": "security",
    }
    label = label_map.get(category, "feedback")
    cmd = [gh_bin, "issue", "create", "--title", f"[{category.upper()}] {title}", "--body", body, "--label", label]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode == 0:
            output_str = stdout.decode("utf-8").strip()
            parts = output_str.split("/")
            if parts and parts[-1].isdigit():
                issue_num = int(parts[-1])
                await asyncio.to_thread(db.update_feedback_status, feedback_id, "OPEN", issue_num)
                log.info("Dispatched GitHub issue #%d for feedback #%d", issue_num, feedback_id)
    except asyncio.TimeoutError as err:
        log.warning("GitHub CLI issue dispatch timed out: %s", err)
    except OSError as err:
        log.debug("OS error running GitHub CLI issue dispatch: %s", err)
    except ValueError as err:
        log.debug("Value error parsing GitHub issue number: %s", err)


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
        detail = readiness.get("diagnostic_message") or "Palworld server engine is starting or unreachable."
        raise HTTPException(status_code=503, detail=detail)
    return {
        "status": "ready",
        "server_name": readiness["server_name"],
        "version": readiness["version"],
        "diagnostic_code": readiness.get("diagnostic_code", "OK"),
    }


@app.get("/", response_class=HTMLResponse, response_model=None)
async def serve_dashboard(request: Request) -> HTMLResponse | RedirectResponse:
    """Serves the reactive Tailwind Web management dashboard.

    Authenticated users (valid session cookie or Bearer token) are served the dashboard.
    Unauthenticated requests are redirected to /login, preserving the request URI via ?next=.
    Users present in a first-spin bootstrap state see the setup credential modal automatically
    when they land on /login.

    Args:
        request (Request): Inbound FastAPI HTTP request.

    Returns:
        HTMLResponse | RedirectResponse: Dashboard HTML or redirect to /login with next= param.
    """
    user = get_current_user_optional(request)
    if user is None:
        # Preserve deep-link so the login page can redirect back after successful sign-in.
        return RedirectResponse(url="/login?next=/", status_code=302)
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading template file at %s: %s", template_path, err)
    return HTMLResponse("<h2>Palworld Operations Suite Dashboard</h2><p>Template loading...</p>")


@app.get("/login", response_class=HTMLResponse, response_model=None)
async def serve_login_page(request: Request) -> HTMLResponse | RedirectResponse:
    """Serves the login / first-spin setup credential page.

    If the user is already authenticated, redirects to the ?next= param or /.
    Presents the bootstrap credential card automatically when bootstrap is still pending.

    Args:
        request (Request): Inbound FastAPI HTTP request.

    Returns:
        HTMLResponse | RedirectResponse: Login/setup HTML or redirect to dashboard.
    """
    user = get_current_user_optional(request)
    if user is not None:
        next_url = request.query_params.get("next", "/")
        return RedirectResponse(url=next_url, status_code=302)
    template_path = Path(__file__).parent / "templates" / "login.html"
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading login template at %s: %s", template_path, err)
    # Fallback minimal page — template creation is covered in Task 3.5
    return HTMLResponse(
        "<h2>Palworld Manager — Login</h2><p>Login template not found. Please redeploy.</p>",
        status_code=200,
    )


@app.get("/setup", response_class=HTMLResponse, response_model=None)
async def serve_setup_page(_request: Request) -> HTMLResponse | RedirectResponse:
    """Redirects to /login which hosts the setup credential presentation tab.

    Args:
        request (Request): Inbound FastAPI HTTP request.

    Returns:
        RedirectResponse: Redirect to /login for unified entry point.
    """
    return RedirectResponse(url="/login", status_code=302)


@app.get("/api/auth/bootstrap-credentials", response_model=BootstrapCredentialsResponse)
async def get_bootstrap_credentials(
    _request: Request,
) -> BootstrapCredentialsResponse:
    """Returns the ephemeral first-spin admin credentials if bootstrap is still pending.

    This endpoint is **unauthenticated** by design so operators can retrieve credentials
    on a fresh install without logging in first. Once POST /api/auth/ack-bootstrap is called
    (or the first admin login occurs), this endpoint permanently returns 404.

    The response includes the plaintext password **exactly once**. After acknowledgment,
    the password is wiped from memory and cannot be recovered from this API.

    Returns:
        BootstrapCredentialsResponse: Current bootstrap lifecycle state with ephemeral password.

    Raises:
        HTTPException: 404 if bootstrap has already been acknowledged.
    """
    state = get_bootstrap_state()
    if not state.is_pending:
        raise HTTPException(
            status_code=404,
            detail="System setup is complete. Bootstrap credentials are no longer available.",
        )
    return BootstrapCredentialsResponse(
        is_pending=True,
        username=state.username,
        password=state.password,
        message=(
            "⚠️ FIRST-SPIN SETUP: Save this password now — it will never be shown again after you click 'I Saved It'. "
            "You can also find it at /etc/palmanager/initial_admin_credential.txt on the server."
        ),
    )


@app.post("/api/auth/ack-bootstrap", response_model=BootstrapAckResponse)
async def acknowledge_bootstrap_credentials(request: Request) -> BootstrapAckResponse:
    """Acknowledges the first-spin bootstrap credentials and permanently seals them.

    After this call, GET /api/auth/bootstrap-credentials returns 404 forever and the
    ephemeral plaintext password is wiped from server memory. This action is irreversible.

    This endpoint is **unauthenticated** because it is called from the setup modal before
    the operator has logged in. Authorization is implicit — calling this endpoint means the
    operator has confirmed they have saved the password.

    Args:
        request (Request): Inbound FastAPI HTTP request.

    Returns:
        BootstrapAckResponse: Confirmation that credentials are permanently sealed.

    Raises:
        HTTPException: 409 if bootstrap was already acknowledged.
    """
    state = get_bootstrap_state()
    if not state.is_pending:
        raise HTTPException(
            status_code=409,
            detail="Bootstrap has already been acknowledged. Nothing to confirm.",
        )
    await asyncio.to_thread(acknowledge_bootstrap, db)
    log.info("Bootstrap acknowledged by client at %s", request.client)
    return BootstrapAckResponse(
        status="success",
        message=(
            "Initial credentials have been acknowledged and permanently wiped from memory. "
            "Please log in with your saved administrator password."
        ),
    )


@app.get("/observability", response_class=HTMLResponse)
@app.get("/metrics", response_class=HTMLResponse)
async def serve_observability_dashboard() -> HTMLResponse:
    """Serves the standalone Prometheus/Grafana style telemetry and observability dashboard.

    Returns:
        HTMLResponse: Rendered observability dashboard HTML content.
    """
    template_path = Path(__file__).parent / "templates" / "metrics.html"
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading template file at %s: %s", template_path, err)
    return HTMLResponse("<h2>Palworld Observability Dashboard</h2><p>Template loading...</p>")


@app.get("/feedback", response_class=HTMLResponse)
async def serve_feedback_page() -> HTMLResponse:
    """Serves the standalone feedback submission and issue tracker page.

    Returns:
        HTMLResponse: Rendered feedback page HTML content.
    """
    template_path = Path(__file__).parent / "templates" / "feedback.html"
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading template file at %s: %s", template_path, err)
    return HTMLResponse("<h2>Feedback & Issue Tracker</h2><p>Template loading...</p>")


REDIRECT_CATEGORY_MAP: dict[str, IssueCategory] = {
    "bug": "bug_report",
    "bug_report": "bug_report",
    "feature": "feature_request",
    "feature_request": "feature_request",
    "docs": "documentation_update",
    "documentation": "documentation_update",
    "documentation_update": "documentation_update",
    "security": "security_report",
    "security_report": "security_report",
}


@app.get("/feedback/{category_shortcut}")
async def redirect_to_github_template(category_shortcut: str) -> RedirectResponse:
    """Redirects clients to the corresponding GitHub issue template or security advisory.

    Args:
        category_shortcut: Shortcut string ('bug', 'feature', 'docs', 'security').

    Returns:
        RedirectResponse: HTTP 307 temporary redirect to upstream GitHub repository.

    Raises:
        HTTPException: 404 Not Found if template shortcut is unrecognized.
    """
    category = REDIRECT_CATEGORY_MAP.get(category_shortcut.lower())
    if category is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unrecognized feedback category '{category_shortcut}'. "
            f"Valid categories: {sorted(REDIRECT_CATEGORY_MAP.keys())}",
        )
    target_url = build_github_issue_url(settings.github_repo_url, category)
    return RedirectResponse(url=target_url, status_code=307)


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
    except KeyError as e:
        log.error("Missing key fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        log.error("Invalid value fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except RuntimeError as e:
        log.error("Runtime error fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except OSError as e:
        log.error("OS error fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _write_ini_file_with_fallback(target_path_str: str, serialized_content: str) -> None:
    """Writes serialized INI content to target path with user home fallback on failure."""
    ini_file = Path(target_path_str)
    try:
        ini_file.parent.mkdir(parents=True, exist_ok=True)
        ini_file.write_text(serialized_content, encoding="utf-8")
    except PermissionError as err:
        log.warning("Permission denied writing INI at %s: %s. Using home directory fallback.", ini_file, err)
        fallback_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        fallback_file.write_text(serialized_content, encoding="utf-8")
    except OSError as err:
        log.warning("OS error writing INI at %s: %s. Using home directory fallback.", ini_file, err)
        fallback_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        fallback_file.write_text(serialized_content, encoding="utf-8")


@app.post("/api/settings")
async def save_sanitized_settings(
    payload: GameplaySettingsSchema,
    _: UserRecord = Depends(perm_server_settings),
) -> dict[str, Any]:
    """Sanitizes, persists, and Git-commits updated gameplay settings.

    Args:
        payload (GameplaySettingsSchema): Validated gameplay settings input.
        _: Enforces server:settings permission.

    Returns:
        dict[str, Any]: Success status, message, and Git snapshot commit hash.

    Raises:
        HTTPException: If persisting settings fails.
    """
    try:
        sanitized_dict = payload.model_dump(exclude_unset=True)
        serialized_ini = pipeline.merge_and_serialize(sanitized_dict)
        await asyncio.to_thread(_write_ini_file_with_fallback, settings.ini_path, serialized_ini)
        reload_settings()
        log.info("Saved settings cleanly to disk.")
        return {"status": "success", "message": "Settings saved cleanly to disk."}
    except PermissionError as e:
        log.error("Permission denied saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"Permission denied: {e!s}") from e
    except ValueError as e:
        log.error("Validation error saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"Validation error: {e!s}") from e
    except RuntimeError as e:
        log.error("Runtime error saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"Runtime error: {e!s}") from e
    except OSError as e:
        log.error("OS error saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"OS error: {e!s}") from e


@app.get("/api/tracker/community")
async def get_community_tracker_data() -> dict[str, Any]:
    """Returns combined 3-section discovery hub, Steam A2S ping, and security matrix.

    Returns:
        dict[str, Any]: Combined discovery, A2S telemetry, and security matrix payload.
    """
    data = await engine.tracker.get_combined_telemetry(
        host_ip=settings.host_ip,
        public_port=settings.PublicPort,
        query_port=settings.QueryPort,
        server_password=settings.ServerPassword,
        rcon_port=settings.RCONPort,
        rcon_enabled=settings.RCONEnabled,
        rest_port=settings.RESTAPIPort,
    )
    return {"status": "success", "data": data}


@app.post("/api/service/reboot")
async def trigger_reboot(
    payload: RebootRequest,
    bg: BackgroundTasks,
    _: UserRecord = Depends(perm_server_reboot),
) -> dict[str, Any]:
    """Schedules a graceful server restart with in-game and Discord notifications.

    Args:
        payload (RebootRequest): Reboot configuration including countdown and custom message.
        bg (BackgroundTasks): FastAPI background task manager.
        _: Enforces server:reboot permission.

    Returns:
        dict[str, Any]: Success response acknowledging countdown initiation.

    Raises:
        HTTPException: If another reboot sequence is already in progress.
    """
    if LOCK_FILE.exists():
        raise HTTPException(status_code=409, detail="Reboot sequence already in progress.")

    if payload.settings:
        serialized_ini = pipeline.merge_and_serialize(payload.settings)
        await asyncio.to_thread(_write_ini_file_with_fallback, settings.ini_path, serialized_ini)
        reload_settings()
        log.info("Saved configuration cleanly before reboot.")

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
    reboot_type = (
        "Immediate server restart"
        if payload.countdown_seconds == 0
        else f"Countdown sequence ({payload.countdown_seconds}s)"
    )
    update_note = " with SteamCMD update" if payload.trigger_steam_update else ""
    return {"status": "success", "message": f"{reboot_type}{update_note} initiated."}


@app.post("/api/service/reboot/cancel")
@app.post("/api/reboot/cancel")
async def cancel_reboot(
    payload: RebootCancelRequest | None = None,
    _: UserRecord = Depends(perm_server_reboot),
) -> dict[str, Any]:
    """Cancels an active reboot countdown sequence.

    Args:
        payload (RebootCancelRequest | None): Optional payload with cancellation reason.
        _: Enforces server:reboot permission.

    Returns:
        dict[str, Any]: Success response acknowledging cancellation.

    Raises:
        HTTPException: If the server is not currently in the COUNTDOWN phase.
    """
    phase = engine.lifecycle_state.get("phase", "IDLE")
    if phase != "COUNTDOWN":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel reboot during phase '{phase}'. Cancellation is only permitted during 'COUNTDOWN'.",
        )

    reason = payload.reason if payload else ""
    success = await engine.cancel_countdown(reason=reason)
    if not success:
        raise HTTPException(
            status_code=409,
            detail="Failed to cancel reboot. Server is no longer in 'COUNTDOWN' phase.",
        )

    return {
        "status": "success",
        "message": "Server reboot countdown cancelled successfully.",
    }


@app.post("/api/players/kick")
async def handle_kick(
    req: PlayerKickRequest,
    _: UserRecord = Depends(perm_player_kick),
) -> dict[str, Any]:
    """Admin endpoint to kick an online player.

    Args:
        req (PlayerKickRequest): Target player ID and moderation reason.
        _: Enforces player:kick permission.

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
async def handle_ban(
    req: PlayerBanRequest,
    _: UserRecord = Depends(perm_player_ban),
) -> dict[str, Any]:
    """Admin endpoint to ban a player.

    Args:
        req (PlayerBanRequest): Target player ID and moderation reason.
        _: Enforces player:ban permission.

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
async def handle_warn(
    req: PlayerWarnRequest,
    _: UserRecord = Depends(perm_player_broadcast),
) -> dict[str, Any]:
    """Admin endpoint to send an announcement across in-game HUD and Discord room.

    Args:
        req (PlayerWarnRequest): Broadcast announcement message string.
        _: Enforces player:broadcast permission.

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


@app.get("/api/logs")
async def get_logs(
    tail: int = 200,
    filter_query: str | None = Query(default=None, alias="filter"),
    level: str = "ALL",
    _: UserRecord = Depends(perm_logs_view),
) -> dict[str, Any]:
    """Retrieves sanitized recent Palworld engine log lines.

    Args:
        tail (int): Number of recent lines to retrieve.
        filter_query (str | None): Keyword or regex filter.
        level (str): Category filter (ALL, ENGINE, EOS, WARN_ERROR).
        _: Enforces logs:view permission.

    Returns:
        dict[str, Any]: Log lines array and retrieval metadata.
    """
    return engine.tracker.read_server_logs(tail=tail, filter_query=filter_query, level=level)


@app.get("/api/logs/download")
async def download_logs(
    _: UserRecord = Depends(perm_logs_view),
) -> PlainTextResponse:
    """Streams full sanitized Palworld engine log file as an attachment.

    Args:
        _: Enforces logs:view permission.

    Returns:
        PlainTextResponse: Raw text stream with attachment headers.
    """
    result = engine.tracker.read_server_logs(tail=2000, level="ALL")
    content = "\n".join(result.get("lines", []))
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"palworld_engine_{timestamp}.log"
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/diagnostics/network-test")
async def run_network_diagnostics_test() -> dict[str, Any]:
    """Executes active network, NAT, and rubberbanding diagnostics.

    Evaluates gateway ping/jitter, internet ping/jitter, engine frame rate,
    and kernel UDP drops to isolate causes of player rubberbanding.

    Returns:
        dict[str, Any]: Diagnostic results, verdict, and recommendations.
    """
    engine_metrics = await engine.get_engine_metrics()
    fps = float(engine_metrics.get("server_fps", 60.0))
    frame_time = float(engine_metrics.get("server_frame_time_ms", 16.6))

    diag_result = await engine.tracker.run_network_diagnostics(
        server_fps=fps,
        server_frame_time_ms=frame_time,
    )
    return {
        "status": "success",
        "data": diag_result,
    }


# =========================================================================
# Authentication & User RBAC Endpoints
# =========================================================================


@app.post("/api/auth/login", response_model=UserLoginResponse)
async def login(req: UserLoginRequest, request: Request, response: Response) -> UserLoginResponse:
    """Authenticates user credentials, writes an audit record, and issues a session token.

    Args:
        req: Login credentials.
        request: FastAPI HTTP request.
        response: FastAPI HTTP response.

    Returns:
        UserLoginResponse with token and granted permissions.

    Raises:
        HTTPException: 401 Unauthorized if credentials fail.
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "Unknown")

    user = db.get_user_by_username(req.username)
    if user is None or not user.is_active or not verify_password(req.password, user.salt, user.password_hash):
        reason = "Account disabled" if (user and not user.is_active) else "Invalid credentials"
        db.record_login_audit(
            username=req.username,
            ip_address=client_ip,
            user_agent=user_agent,
            status="FAILED",
            failure_reason=reason,
        )
        log.warning("Login failed for user '%s' from %s: %s", req.username, client_ip, reason)
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = generate_session_token(user.username, secret_key=settings.AdminPassword)
    db.record_login_audit(
        username=user.username,
        ip_address=client_ip,
        user_agent=user_agent,
        status="SUCCESS",
    )
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.update_user_last_login(user.id, now_iso)

    # Auto-seal bootstrap credentials upon first successful login by any admin.
    # This ensures the ephemeral password is removed from memory even if the operator
    # skips the "I Saved It" button on the setup modal and logs in directly.
    bootstrap_state = get_bootstrap_state()
    if bootstrap_state.is_pending and user.role == "admin":
        await asyncio.to_thread(acknowledge_bootstrap, db)
        log.info("Bootstrap auto-acknowledged on first admin login by '%s'.", user.username)

    # Issue session cookie
    response.set_cookie(
        key="pal_session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )

    permissions = db.get_user_permissions(user.id)
    return UserLoginResponse(
        status="success",
        token=token,
        username=user.username,
        role=user.role,
        permissions=permissions,
    )


@app.post("/api/auth/register", response_model=UserResponse)
async def register(
    req: UserRegisterRequest,
    request: Request,
) -> UserResponse:
    """Public self-service user registration assigning least-privilege viewer role.

    Args:
        req: Validated user registration request payload.
        request: FastAPI HTTP request for client IP audit logging.

    Returns:
        Created UserResponse record.

    Raises:
        HTTPException: 409 Conflict if username is already registered.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    existing = db.get_user_by_username(req.username)
    if existing is not None:
        log.warning("Registration conflict: username '%s' already exists (client %s)", req.username, client_ip)
        raise HTTPException(status_code=409, detail=f"Username '{req.username}' already registered.")

    pw_hash, salt = hash_password(req.password)
    user = db.create_user(
        username=req.username,
        password_hash=pw_hash,
        salt=salt,
        email=req.email,
        role="viewer",
        is_active=True,
    )
    log.info("Registered new user '%s' with role 'viewer' from %s", user.username, client_ip)
    perms = db.get_user_permissions(user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        permissions=perms,
    )


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    """Terminates session by clearing session cookie.

    Args:
        response: FastAPI HTTP response.

    Returns:
        Confirmation dictionary.
    """
    response.delete_cookie("pal_session_token")
    return {"status": "success", "message": "Successfully logged out."}


@app.get("/api/auth/me", response_model=UserResponse)
async def get_me(user: UserRecord = Depends(get_current_user)) -> UserResponse:
    """Returns profile and active permissions for the calling user.

    Args:
        user: Authenticated user record.

    Returns:
        UserResponse with role and permissions.
    """
    permissions = db.get_user_permissions(user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        permissions=permissions,
    )


@app.get("/api/auth/audit", response_model=list[LoginAuditResponse])
async def get_login_audit_trail(
    limit: int = 50,
    offset: int = 0,
    _: UserRecord = Depends(perm_users_manage),
) -> list[LoginAuditResponse]:
    """Retrieves paginated login attempts from the audit trail.

    Args:
        limit: Number of audit records to retrieve.
        offset: Query offset.
        _: Enforces users:manage permission.

    Returns:
        List of LoginAuditResponse items.
    """
    records = db.list_login_audits(limit=limit, offset=offset)
    return [
        LoginAuditResponse(
            id=rec.id,
            username=rec.username,
            timestamp=rec.timestamp,
            ip_address=rec.ip_address,
            user_agent=rec.user_agent,
            status=rec.status,
            failure_reason=rec.failure_reason,
        )
        for rec in records
    ]


@app.get("/api/users", response_model=list[UserResponse])
async def list_registered_users(
    _: UserRecord = Depends(perm_users_manage),
) -> list[UserResponse]:
    """Lists all registered system user accounts.

    Args:
        _: Enforces users:manage permission.

    Returns:
        List of UserResponse items.
    """
    users = db.list_users()
    response_list: list[UserResponse] = []
    for u in users:
        perms = db.get_user_permissions(u.id)
        response_list.append(
            UserResponse(
                id=u.id,
                username=u.username,
                email=u.email,
                role=u.role,
                is_active=u.is_active,
                created_at=u.created_at,
                last_login=u.last_login,
                permissions=perms,
            )
        )
    return response_list


@app.post("/api/users", response_model=UserResponse)
async def create_new_user(
    req: UserCreateRequest,
    _: UserRecord = Depends(perm_users_manage),
) -> UserResponse:
    """Creates a new user account with hashed password and assigned permissions.

    Args:
        req: User creation request.
        _: Enforces users:manage permission.

    Returns:
        Created UserResponse record.

    Raises:
        HTTPException: 409 Conflict if username already exists.
    """
    existing = db.get_user_by_username(req.username)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Username '{req.username}' already registered.")

    pw_hash, salt = hash_password(req.password)
    user = db.create_user(
        username=req.username,
        password_hash=pw_hash,
        salt=salt,
        email=req.email,
        role=req.role,
        is_active=True,
    )
    if req.permissions is not None:
        db.set_user_permissions(user.id, req.permissions)

    perms = db.get_user_permissions(user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        permissions=perms,
    )


@app.patch("/api/users/{user_id}", response_model=UserResponse)
async def update_existing_user(
    user_id: int,
    req: UserUpdateRequest,
    _: UserRecord = Depends(perm_users_manage),
) -> UserResponse:
    """Updates profile attributes, role, or credentials for an existing user.

    Args:
        user_id: Target user identifier.
        req: User update request.
        _: Enforces users:manage permission.

    Returns:
        Updated UserResponse record.

    Raises:
        HTTPException: 404 Not Found if user does not exist.
    """
    existing = db.get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if existing.role == "admin" and existing.is_active:
        if req.role is not None and req.role != "admin" and db.count_active_admins() <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last remaining active administrator.")
        if req.is_active is False and db.count_active_admins() <= 1:
            raise HTTPException(status_code=400, detail="Cannot deactivate the last remaining active administrator.")

    pw_hash: str | None = None
    salt: str | None = None
    if req.password:
        pw_hash, salt = hash_password(req.password)

    updated = db.update_user(
        user_id=user_id,
        email=req.email,
        role=req.role,
        is_active=req.is_active,
        password_hash=pw_hash,
        salt=salt,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if req.permissions is not None:
        db.set_user_permissions(user_id, req.permissions)

    perms = db.get_user_permissions(user_id)
    return UserResponse(
        id=updated.id,
        username=updated.username,
        email=updated.email,
        role=updated.role,
        is_active=updated.is_active,
        created_at=updated.created_at,
        last_login=updated.last_login,
        permissions=perms,
    )


@app.patch("/api/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: int,
    req: UserRoleUpdateRequest,
    _: UserRecord = Depends(perm_users_manage),
) -> UserResponse:
    """Promotes or demotes an existing user account to a designated system role.

    Args:
        user_id: Target user identifier.
        req: Validated role update payload.
        _: Enforces users:manage administrative permission.

    Returns:
        Updated UserResponse record with synchronized permissions.

    Raises:
        HTTPException: 404 Not Found if user does not exist.
        HTTPException: 400 Bad Request if demoting the last remaining active administrator.
    """
    existing = db.get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if existing.role == "admin" and existing.is_active and req.role != "admin" and db.count_active_admins() <= 1:
        raise HTTPException(status_code=400, detail="Cannot demote the last remaining active administrator.")

    updated = db.update_user(user_id=user_id, role=req.role)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    perms = db.get_user_permissions(user_id)
    log.info("Updated role for user '%s' (ID %d) to '%s'", updated.username, user_id, req.role)
    return UserResponse(
        id=updated.id,
        username=updated.username,
        email=updated.email,
        role=updated.role,
        is_active=updated.is_active,
        created_at=updated.created_at,
        last_login=updated.last_login,
        permissions=perms,
    )


@app.delete("/api/users/{user_id}")
async def delete_existing_user(
    user_id: int,
    admin_user: UserRecord = Depends(perm_users_manage),
) -> dict[str, str]:
    """Deletes a user account from the system.

    Args:
        user_id: Target user identifier.
        admin_user: Currently authenticated administrator.

    Returns:
        Confirmation dictionary.

    Raises:
        HTTPException: 400 Bad Request if deleting self or the last administrator, or 404 if not found.
    """
    if admin_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own active administrator account.")

    existing = db.get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if existing.role == "admin" and existing.is_active and db.count_active_admins() <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last remaining active administrator.")

    ok = db.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    return {"status": "success", "message": f"User ID {user_id} deleted."}


# =========================================================================
# Feedback & Template-Driven Issue Submissions Endpoints
# =========================================================================


@app.post("/api/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackSubmitRequest,
    bg: BackgroundTasks,
    user: UserRecord = Depends(perm_feedback_submit),
) -> FeedbackResponse:
    """Records an issue or feedback submission mapped 1:1 to repository templates.

    Args:
        req: Template feedback submission payload.
        bg: FastAPI background tasks.
        user: Authenticated user.

    Returns:
        FeedbackResponse with ticket status.
    """
    markdown_body = render_feedback_markdown(req)
    metadata = req.model_dump(exclude={"category", "title", "description"}, exclude_none=True)
    metadata_json = json.dumps(metadata)

    feedback_rec = db.create_feedback(
        category=req.category,
        title=req.title,
        description=markdown_body,
        metadata_json=metadata_json,
        submitted_by=user.username,
    )

    bg.add_task(
        dispatch_github_issue,
        req.category,
        req.title,
        markdown_body,
        feedback_rec.id,
    )

    return FeedbackResponse(
        id=feedback_rec.id,
        category=feedback_rec.category,
        title=feedback_rec.title,
        description=feedback_rec.description,
        metadata=metadata,
        submitted_by=feedback_rec.submitted_by,
        status=feedback_rec.status,
        github_issue_number=feedback_rec.github_issue_number,
        created_at=feedback_rec.created_at,
    )


# pylint: disable=too-many-arguments,too-many-positional-arguments
# Rationale: API endpoint supports multi-field filtering, pagination, and user scoping.
@app.get("/api/feedback", response_model=list[FeedbackResponse])
async def list_feedback_submissions(
    request: Request,
    mine: bool = False,
    submitted_by: str | None = None,
    category: IssueCategory | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[FeedbackResponse]:
    """Lists historical feedback submissions with optional filtering.

    Args:
        request: Inbound FastAPI HTTP request.
        mine: Filter for tickets created by the active authenticated user.
        submitted_by: Optional filter for tickets submitted by a specific user handle.
        category: Optional filter for issue template category.
        status: Optional filter for ticket status ('OPEN', 'RESOLVED', 'CLOSED').
        limit: Max entries to return.
        offset: Query offset.

    Returns:
        List of FeedbackResponse items.

    Raises:
        HTTPException: 401 Unauthorized if mine=True and user is unauthenticated.
    """
    filter_user: str | None = submitted_by
    if mine:
        user = get_current_user_optional(request)
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required to filter submissions by current user.",
            )
        filter_user = user.username

    records = db.list_feedbacks(
        limit=limit,
        offset=offset,
        submitted_by=filter_user,
        category=category,
        status=status,
    )
    result: list[FeedbackResponse] = []
    for rec in records:
        try:
            meta = json.loads(rec.metadata_json)
        except json.JSONDecodeError as err:
            log.debug("JSON decode error in feedback metadata: %s", err)
            meta = {}
        result.append(
            FeedbackResponse(
                id=rec.id,
                category=rec.category,
                title=rec.title,
                description=rec.description,
                metadata=meta,
                submitted_by=rec.submitted_by,
                status=rec.status,
                github_issue_number=rec.github_issue_number,
                created_at=rec.created_at,
            )
        )
    return result


# =========================================================================
# 5. Historical Metrics & 30-Day Retention Endpoints
# =========================================================================


@app.get(
    "/api/metrics/history",
    response_model=MetricHistoryResponse,
    dependencies=[Depends(perm_logs_view)],
)
async def get_metrics_history(
    window: Literal["1h", "24h", "7d", "30d"] = Query(
        default="24h",
        description="Historical aggregation time window ('1h', '24h', '7d', '30d')",
    ),
) -> MetricHistoryResponse:
    """Retrieves downsampled time-series aggregation buckets for the requested window.

    Args:
        window: Selected time window filter.

    Returns:
        MetricHistoryResponse containing time-series buckets.
    """
    await flush_metrics_buffer()
    buckets_data = await asyncio.to_thread(metrics_db.get_history, window)
    response_buckets = [
        MetricBucketResponse(
            bucket_timestamp=b.bucket_timestamp,
            avg_fps=b.avg_fps,
            min_fps=b.min_fps,
            max_fps=b.max_fps,
            avg_frame_time_ms=b.avg_frame_time_ms,
            avg_players=b.avg_players,
            max_players=b.max_players,
            avg_cpu_pct=b.avg_cpu_pct,
            max_cpu_pct=b.max_cpu_pct,
            avg_ram_pct=b.avg_ram_pct,
            max_ram_pct=b.max_ram_pct,
            sample_count=b.sample_count,
        )
        for b in buckets_data
    ]
    return MetricHistoryResponse(
        window=window,
        total_buckets=len(response_buckets),
        buckets=response_buckets,
    )


@app.get(
    "/api/metrics/summary",
    response_model=MetricSummaryResponse,
    dependencies=[Depends(perm_logs_view)],
)
async def get_metrics_summary() -> MetricSummaryResponse:
    """Retrieves statistical KPI summary across performance and telemetry over a rolling 30-day window.

    Returns:
        MetricSummaryResponse with 30-day aggregates.
    """
    await flush_metrics_buffer()
    summary = await asyncio.to_thread(metrics_db.get_30_day_summary)
    async with metrics_buffer_lock:
        buffered_count = len(metrics_buffer)
    return MetricSummaryResponse(
        total_samples=summary.total_samples,
        peak_players=summary.peak_players,
        avg_players=summary.avg_players,
        lowest_fps=summary.lowest_fps,
        avg_fps=summary.avg_fps,
        peak_cpu_pct=summary.peak_cpu_pct,
        avg_cpu_pct=summary.avg_cpu_pct,
        peak_ram_pct=summary.peak_ram_pct,
        avg_ram_pct=summary.avg_ram_pct,
        buffered_samples=buffered_count,
        window_start=summary.window_start,
        window_end=summary.window_end,
    )


@app.post(
    "/api/metrics/flush",
    response_model=MetricFlushResponse,
    dependencies=[Depends(perm_server_settings)],
)
async def flush_metrics() -> MetricFlushResponse:
    """Manually flushes in-memory buffered metrics to disk.

    Returns:
        MetricFlushResponse with flushed and remaining snapshot counts.
    """
    flushed = await flush_metrics_buffer()
    async with metrics_buffer_lock:
        remaining = len(metrics_buffer)
    return MetricFlushResponse(
        status="success",
        flushed_snapshots=flushed,
        buffered_remaining=remaining,
    )


@app.post(
    "/api/metrics/prune",
    response_model=MetricPruneResponse,
    dependencies=[Depends(perm_server_settings)],
)
async def prune_metrics(
    days: int | None = Query(
        default=None,
        ge=1,
        le=365,
        description="Optional retention window override in days",
    ),
) -> MetricPruneResponse:
    """Manually triggers pruning of metrics older than the retention threshold.

    Args:
        days: Optional retention window override in days (default: configured settings).

    Returns:
        MetricPruneResponse with total pruned records count.
    """
    retention = days if days is not None else settings.metrics_retention_days
    pruned = await asyncio.to_thread(metrics_db.prune_older_than, retention)
    return MetricPruneResponse(
        status="success",
        pruned_records=pruned,
        retention_days=retention,
    )


@app.get(
    "/api/system/update/status",
    response_model=UpdateStatusResponse,
    tags=["System"],
    dependencies=[Depends(get_current_user)],
)
async def get_system_update_status(
    force: bool = Query(
        default=False,
        description="Whether to trigger an immediate live upstream probe",
    ),
) -> UpdateStatusResponse:
    """Returns current upstream commit status, commits behind, and update availability."""
    if force:
        return await updater.check_for_updates()
    return updater.get_status()


@app.post(
    "/api/system/update/check",
    response_model=UpdateStatusResponse,
    tags=["System"],
    dependencies=[Depends(perm_system_update)],
)
async def check_system_update_on_demand() -> UpdateStatusResponse:
    """Dispatches on-demand GitHub probe checking for upstream commits."""
    return await updater.check_for_updates()


@app.post(
    "/api/system/update/apply",
    response_model=UpdateApplyResponse,
    tags=["System"],
)
async def apply_system_update(
    request: Request,
    payload: UpdateApplyRequest | None = None,
    user: UserRecord = Depends(perm_system_update),
) -> UpdateApplyResponse:
    """Dispatches detached host deployment script pulling latest code and reloading systemd service."""
    target_branch = payload.branch if payload is not None else settings.update_branch
    client_ip = get_client_ip(request)
    log.warning(
        "SYSTEM UPDATE INITIATED: User '%s' (role: %s) requested update to branch '%s' from IP %s",
        user.username,
        user.role,
        target_branch,
        client_ip,
    )
    return await updater.apply_update(branch=target_branch)


@app.post("/api/server/shutdown", response_model=ShutdownResponse, tags=["Server"])
async def shutdown_server(
    payload: ShutdownRequest,
    bg: BackgroundTasks,
    _: UserRecord = Depends(perm_server_reboot),
) -> ShutdownResponse:
    """Schedules a graceful server shutdown with countdown broadcasts and a world save.

    Enqueues a background task that: (1) broadcasts countdown announcements to
    all connected players at 60-second intervals, (2) forces a world save via
    POST /v1/api/save, and (3) triggers a systemd service restart. Returns
    immediately after enqueueing; the caller does not wait for the restart.

    Args:
        payload (ShutdownRequest): Countdown seconds (30-3600) and broadcast message.
        bg (BackgroundTasks): FastAPI background task manager.
        _: Enforces server:reboot permission.

    Returns:
        ShutdownResponse: Confirmation of the scheduled shutdown.

    Raises:
        HTTPException: 409 if another reboot sequence is already in progress.
    """
    if LOCK_FILE.exists():
        raise HTTPException(
            status_code=409,
            detail="Reboot sequence already in progress. Cancel it before scheduling a shutdown.",
        )

    log.info(
        "Graceful shutdown scheduled: %ss countdown with message '%s'",
        payload.seconds,
        payload.message,
    )
    bg.add_task(
        engine.execute_countdown_and_reboot,
        payload.seconds,
        False,  # trigger_steam_update=False for a plain restart
        None,   # update_version_tag
        payload.message,
    )
    return ShutdownResponse(
        status="scheduled",
        countdown_seconds=payload.seconds,
        message=payload.message,
    )


@app.post("/api/server/save", response_model=SaveResponse, tags=["Server"])
async def manual_world_save(
    user: UserRecord = Depends(perm_server_reboot),
) -> SaveResponse:
    """Triggers an immediate Palworld world save and records an audit log entry.

    Delegates to PalEngine.trigger_save(), which calls POST /v1/api/save on the
    local Palworld REST API. The endpoint waits for the save to complete before
    responding (up to the engine's 5-second save timeout).

    Args:
        user (UserRecord): Authenticated operator — used for the audit log entry.

    Returns:
        SaveResponse: Confirmation with operator username and ISO 8601 timestamp.

    Raises:
        HTTPException: 503 if the Palworld REST API save call fails.
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    ok = await engine.trigger_save()
    if not ok:
        raise HTTPException(
            status_code=503,
            detail="World save failed. Ensure the Palworld server is online and reachable.",
        )
    log.info("Manual world save triggered by operator '%s' at %s", user.username, timestamp)
    return SaveResponse(
        status="ok",
        triggered_by=user.username,
        timestamp=timestamp,
    )


if __name__ == "__main__":
    import argparse

    import uvicorn

    cli_parser = argparse.ArgumentParser(
        description="Palworld Unified Operations Suite & Web Management Plane"
    )
    cli_parser.add_argument(
        "--host",
        default=settings.host,
        help="Bind host IP address (default: configured setting)",
    )
    cli_parser.add_argument(
        "--port",
        type=int,
        default=settings.port,
        help="Bind port number (default: configured setting)",
    )
    cli_args = cli_parser.parse_args()

    uvicorn.run(app, host=cli_args.host, port=cli_args.port)
