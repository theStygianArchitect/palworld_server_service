"""Palworld Operations Suite & Web Management Plane API Entrypoint.

Provides FastAPI REST endpoints, WebSocket telemetry streaming, configuration management,
and server reboot orchestration in compliance with Google Style Guide and 3 AM standards.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import shutil
import sqlite3
import subprocess  # nosec B404 - required for systemctl liveness probe and cert-manager execution
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.core.config import inspect_certificate, reload_settings
from app.core.logger import log
from app.database import MetricSnapshotRecord, bootstrap_admin_user
from app.engine.service import LOCK_FILE
from app.engine.tls_scheduler import (
    _TLS_ENGINE_AVAILABLE,
    certificate_renewal_scheduler,
    trigger_duckdns_sync,
    trigger_tls_provisioning_check,
)
from app.routers import (
    auth_router,
    feedback_router,
    players_router,
    settings_router,
    system_router,
    telemetry_router,
    ui_router,
)
from app.routers.deps import (
    db,
    engine,
    flush_metrics_buffer,
    get_current_user,
    get_tracker_telemetry_kwargs,
    metrics_buffer,
    metrics_buffer_lock,
    metrics_db,
    notifier,
    pipeline,
    settings,
    updater,
)
from app.routers.settings import stage_settings_for_reboot
from app.server import run_server


async def telemetry_streamer() -> None:
    """Continuous background loop streaming live bare-metal and server telemetry over WebSocket."""
    while True:
        try:
            liveness = False
            if os.name != "nt":
                try:
                    systemctl_bin = shutil.which("systemctl") or "/bin/systemctl"  # nosec B607
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
                max_players=metrics.get("max_players", 32),
                current_players=metrics.get("current_players", 0),
                **get_tracker_telemetry_kwargs(),
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
        except asyncio.CancelledError:
            log.debug("Telemetry streamer received cancellation request.")
            break
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.warning("Unexpected error in telemetry stream: %s", err)
        await asyncio.sleep(2)


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
    tls_provision_task = asyncio.create_task(trigger_tls_provisioning_check())
    renewal_task = asyncio.create_task(certificate_renewal_scheduler())
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
    tls_provision_task.cancel()
    renewal_task.cancel()
    startup_task.cancel()
    stream_task.cancel()
    metrics_task.cancel()
    if updater_task is not None:
        updater_task.cancel()
    try:
        await renewal_task
    except asyncio.CancelledError as err:
        log.debug("Certificate renewal background task cancelled during shutdown: %s", err)
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
    version=__version__,
    lifespan=lifespan,
)

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(ui_router)
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(players_router)
app.include_router(telemetry_router)
app.include_router(system_router)
app.include_router(feedback_router)

if __name__ == "__main__":
    run_server()

__all__ = [
    "LOCK_FILE",
    "_TLS_ENGINE_AVAILABLE",
    "app",
    "certificate_renewal_scheduler",
    "db",
    "engine",
    "get_current_user",
    "inspect_certificate",
    "metrics_db",
    "notifier",
    "pipeline",
    "reload_settings",
    "run_server",
    "settings",
    "stage_settings_for_reboot",
    "trigger_tls_provisioning_check",
    "updater",
]
