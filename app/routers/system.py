"""System Lifecycle & Maintenance Router.

Provides endpoints for server reboot orchestration, TLS certificate management,
update deployment, and health probes, following Google Style Guide and 3 AM standards.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app import __version__
from app.api.schemas import (
    ChangelogResponse,
    DeploymentProgressResponse,
    PostUpdateAcknowledgeResponse,
    RebootCancelRequest,
    RebootRequest,
    SaveResponse,
    ShutdownRequest,
    ShutdownResponse,
    SystemVersionResponse,
    TLSRenewRequest,
    TLSRenewResponse,
    TLSStatusResponse,
    UpdateApplyRequest,
    UpdateApplyResponse,
    UpdateStatusResponse,
)
from app.core.config import inspect_certificate, reload_settings, resolve_ssl_paths
from app.core.logger import log
from app.database import UserRecord
from app.engine.changelog import get_changelog
from app.engine.service import LOCK_FILE
from app.routers.deps import (
    engine,
    get_client_ip,
    get_current_user,
    perm_admin,
    perm_server_reboot,
    perm_system_update,
    perm_view,
    pipeline,
    settings,
    updater,
)
from app.routers.settings import _write_ini_file_with_fallback, stage_settings_for_reboot

router = APIRouter(tags=["System Lifecycle & Maintenance"])

_DEFAULT_CERT_MANAGER_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "palworld-cert-manager.sh"


def _resolve_canonical_url() -> str:
    """Resolves the canonical public HTTPS URL based on configuration.

    Returns:
        str: Absolute canonical HTTPS URL with port.
    """
    raw_domain = str(settings.duckdns_domain or "")
    domain = raw_domain.strip().lower()
    if not domain or domain in ("localhost", "yourdomain.duckdns.org"):
        domain = "thestygianarchitect.duckdns.org"
    return f"https://{domain}:{settings.web_port}"


def _check_auto_renew_active() -> bool:
    """Checks if palworld-cert-renew.timer is present or active on the host."""
    timer_path = Path("/etc/systemd/system/palworld-cert-renew.timer")
    if timer_path.is_file():
        return True
    if os.name != "nt":
        try:
            res = subprocess.run(  # nosec B603, B607
                ["systemctl", "is-active", "--quiet", "palworld-cert-renew.timer"],
                capture_output=True,
                check=False,
                timeout=2,
            )
            return res.returncode == 0
        except subprocess.SubprocessError as err:
            log.debug("Subprocess error checking palworld-cert-renew.timer: %s", err)
            return False
        except OSError as err:
            log.debug("OS error checking palworld-cert-renew.timer: %s", err)
            return False
    return False


@router.get("/health")
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


@router.get("/ready")
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


@router.get("/api/system/version", response_model=SystemVersionResponse)
async def get_system_version(_: UserRecord = Depends(perm_view)) -> SystemVersionResponse:
    """Returns current application semantic version string and canonical URL.

    Protected by standard authentication (perm_view). Accessible to all authenticated roles.

    Returns:
        SystemVersionResponse: Semantic version and canonical URL payload.
    """
    return SystemVersionResponse(
        version=__version__,
        canonical_url=_resolve_canonical_url(),
        tls_active=resolve_ssl_paths(settings) is not None,
    )


@router.get("/api/system/changelog", response_model=ChangelogResponse)
async def get_system_changelog(_: UserRecord = Depends(perm_view)) -> ChangelogResponse:
    """Returns parsed changelog releases, categories, and unreleased roadmap items.

    Protected by standard authentication (perm_view). Accessible to all authenticated roles.
    """
    return get_changelog()


@router.get("/canonical", response_class=RedirectResponse, response_model=None)
async def redirect_canonical() -> RedirectResponse:
    """Redirects client to the canonical HTTPS portal origin.

    Returns:
        RedirectResponse: HTTP 307 redirect targeting canonical HTTPS portal.
    """
    return RedirectResponse(url=f"{_resolve_canonical_url()}/", status_code=307)


@router.post("/api/service/reboot")
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
        engine.stage_settings(serialized_ini)
        stage_settings_for_reboot(serialized_ini)
        reload_settings()
        log.info("Saved and staged configuration cleanly before reboot.")

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


@router.post("/api/service/reboot/cancel")
@router.post("/api/reboot/cancel")
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


@router.post("/api/server/shutdown", response_model=ShutdownResponse)
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
        None,  # update_version_tag
        payload.message,
    )
    return ShutdownResponse(
        status="scheduled",
        countdown_seconds=payload.seconds,
        message=payload.message,
    )


@router.post("/api/server/save", response_model=SaveResponse)
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


@router.get("/api/system/update/status", response_model=UpdateStatusResponse)
async def get_system_update_status(
    force: bool = Query(
        default=False,
        description="Whether to trigger an immediate live upstream probe",
    ),
    _: UserRecord = Depends(get_current_user),
) -> UpdateStatusResponse:
    """Returns current upstream commit status, commits behind, and update availability."""
    if force:
        return await updater.check_for_updates()
    return updater.get_status()


@router.post("/api/system/update/check", response_model=UpdateStatusResponse)
async def check_system_update_on_demand(_: UserRecord = Depends(perm_system_update)) -> UpdateStatusResponse:
    """Dispatches on-demand GitHub probe checking for upstream commits."""
    return await updater.check_for_updates()


@router.post("/api/system/update/apply", response_model=UpdateApplyResponse)
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


@router.get("/api/system/deploy/progress", response_model=DeploymentProgressResponse)
async def get_deployment_progress(_: UserRecord = Depends(get_current_user)) -> DeploymentProgressResponse:
    """Returns real-time deployment progression, step status, elapsed time, and ETA."""
    return updater.get_deployment_progress()


@router.post("/api/system/update/acknowledge", response_model=PostUpdateAcknowledgeResponse)
async def acknowledge_system_update(_: UserRecord = Depends(perm_system_update)) -> PostUpdateAcknowledgeResponse:
    """Acknowledges and dismisses the post-update completion notification banner."""
    updater.acknowledge_last_update()
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return PostUpdateAcknowledgeResponse(status="success", acknowledged_at=timestamp)


@router.get("/api/system/tls/status", response_model=TLSStatusResponse)
async def get_tls_status(
    _: UserRecord = Depends(perm_admin),
) -> TLSStatusResponse:
    """Returns the current TLS encryption status, certificate details, and domain config.

    Restricted strictly to administrators.

    Args:
        _: Authenticated administrator requesting status.

    Returns:
        TLSStatusResponse: Operational status of SSL/TLS and certificate metadata.
    """
    domain = settings.duckdns_domain or "localhost"
    resolved_paths = resolve_ssl_paths(settings)
    auto_renew_active = await asyncio.to_thread(_check_auto_renew_active)

    if resolved_paths is None:
        return TLSStatusResponse(
            enabled=False,
            scheme="http",
            domain=domain,
            port=settings.web_port,
            certificate=None,
            cert_path=None,
            auto_renew_active=auto_renew_active,
            warning="Running unencrypted plaintext HTTP. No valid certificate pair detected.",
            canonical_url=_resolve_canonical_url(),
        )

    cert_path, _key_path = resolved_paths
    cert_info = inspect_certificate(cert_path)
    warning = None
    if cert_info is not None and cert_info.days_remaining <= 15:
        warning = f"Certificate expires in {cert_info.days_remaining} days. Renewal recommended."

    return TLSStatusResponse(
        enabled=True,
        scheme="https",
        domain=domain,
        port=settings.ssl_port if settings.ssl_enabled else settings.web_port,
        certificate=cert_info,
        cert_path=str(cert_path),
        auto_renew_active=auto_renew_active,
        warning=warning,
        canonical_url=_resolve_canonical_url(),
    )


@router.post("/api/system/tls/renew", response_model=TLSRenewResponse)
async def trigger_tls_renewal(
    payload: TLSRenewRequest,
    user: UserRecord = Depends(perm_admin),
) -> TLSRenewResponse:
    """Triggers an on-demand TLS certificate renewal via palworld-cert-manager.sh.

    Restricted strictly to administrators.
    Supports early renewal via payload.force=True.

    Args:
        payload (TLSRenewRequest): Renewal trigger parameters.
        user (UserRecord): Authenticated administrator triggering the action.

    Returns:
        TLSRenewResponse: Outcome status and informational message.

    Raises:
        HTTPException: 500 if the certificate renewal script encounters an error.
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log.info(
        "Administrator '%s' requested TLS certificate renewal (force=%s) at %s",
        user.username,
        payload.force,
        timestamp,
    )

    candidate_scripts = [
        Path("/opt/palworld-web-manager/scripts/palworld-cert-manager.sh"),
        Path(__file__).parent.parent.parent / "scripts" / "palworld-cert-manager.sh",
    ]
    cert_script: Path | None = None
    for s in candidate_scripts:
        if s.is_file():
            cert_script = s
            break

    if cert_script is None:
        log.warning("palworld-cert-manager.sh script not found on host filesystem")
        return TLSRenewResponse(
            status="skipped",
            message="Certificate manager script not installed. Please deploy scripts/palworld-cert-manager.sh.",
            triggered_at=timestamp,
        )

    cmd = [str(cert_script), "renew"]
    if payload.force:
        cmd.append("--force")

    if os.name != "nt":
        sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"
        cmd[0:0] = [sudo_bin, "-n"]

    try:
        proc = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or "Unknown error"
            log.error("Certificate renewal script returned error code %d: %s", proc.returncode, err_msg)
            if "password is required" in err_msg or "terminal is required" in err_msg:
                detail_msg = (
                    "Certificate renewal failed: passwordless sudo is not configured for palworld-cert-manager.sh. "
                    "Please run 'sudo ./scripts/deploy.sh' on the host or provision '/etc/sudoers.d/palmanager-certs'."
                )
            else:
                detail_msg = f"Certificate renewal failed: {err_msg}"
            raise HTTPException(
                status_code=500,
                detail=detail_msg,
            )

        log.info("Certificate renewal completed successfully: %s", proc.stdout.strip())
        return TLSRenewResponse(
            status="success",
            message=f"Certificate renewal completed successfully: {proc.stdout.strip()[:200]}",
            triggered_at=timestamp,
        )
    except subprocess.TimeoutExpired as err:
        log.error("Certificate renewal script timed out after 120s: %s", err)
        raise HTTPException(
            status_code=504,
            detail="Certificate renewal script timed out after 120 seconds. Check DNS propagation.",
        ) from err
    except OSError as err:
        log.error("OS error invoking certificate renewal: %s", err)
        raise HTTPException(
            status_code=500,
            detail=f"OS execution error: {err}",
        ) from err
