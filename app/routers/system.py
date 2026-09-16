"""System Lifecycle & Maintenance Router.

Provides endpoints for server reboot orchestration, TLS certificate management,
update deployment, and health probes, following Google Style Guide and 3 AM standards.
"""

from __future__ import annotations

import asyncio
import datetime
import os
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
    TLSCertificateInfo,
    TLSRenewRequest,
    TLSRenewResponse,
    TLSSettingsUpdateRequest,
    TLSSettingsUpdateResponse,
    TLSStatusResponse,
    UpdateApplyRequest,
    UpdateApplyResponse,
    UpdateStatusResponse,
)
from app.core.config import reload_settings, resolve_ssl_paths
from app.core.logger import log
from app.database import UserRecord
from app.engine.changelog import get_changelog
from app.engine.service import LOCK_FILE
from app.engine.tls_manager import get_tls_certificate_status, provision_tls_certificates
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
    status = get_tls_certificate_status(domain=domain)

    # Compute countdown fields
    next_renewal_at: str | None = None
    renewal_countdown_seconds: int | None = None
    if status.is_valid and status.not_after:
        not_after = status.not_after
        if not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=datetime.timezone.utc)
        renewal_dt = not_after - datetime.timedelta(days=30)
        next_renewal_at = renewal_dt.isoformat()
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        diff = (renewal_dt - now_dt).total_seconds()
        renewal_countdown_seconds = max(0, int(diff))

    auto_renew_active = bool(settings.ssl_auto_renew and settings.ssl_cert_mode != "custom")

    if not status.is_valid:
        return TLSStatusResponse(
            enabled=False,
            scheme="http",
            domain=domain,
            port=settings.web_port,
            certificate=None,
            cert_path=str(status.fullchain_path) if status.fullchain_path else None,
            auto_renew_active=auto_renew_active,
            warning=status.error_message or "Running unencrypted plaintext HTTP. No valid certificate detected.",
            canonical_url=_resolve_canonical_url(),
            cert_mode=settings.ssl_cert_mode,
            next_renewal_at=next_renewal_at,
            renewal_countdown_seconds=renewal_countdown_seconds,
        )

    # Note: Using TLSCertificateInfo (app/api/schemas.py)
    cert_info = TLSCertificateInfo(
        subject=status.domain,
        issuer=status.issuer,
        valid_from=status.not_before.isoformat() + "Z" if status.not_before else "",
        expires_at=status.not_after.isoformat() + "Z" if status.not_after else "",
        days_remaining=status.days_remaining,
        is_expired=not status.is_valid,
        is_self_signed=status.is_self_signed,
        san_list=status.subject_alt_names,
    )

    warning = None
    if status.days_remaining <= 15:
        warning = f"Certificate expires in {status.days_remaining} days. Renewal recommended."

    return TLSStatusResponse(
        enabled=True,
        scheme="https",
        domain=domain,
        port=settings.web_port,
        certificate=cert_info,
        cert_path=str(status.fullchain_path),
        auto_renew_active=auto_renew_active,
        warning=warning,
        canonical_url=_resolve_canonical_url(),
        cert_mode=settings.ssl_cert_mode,
        next_renewal_at=next_renewal_at,
        renewal_countdown_seconds=renewal_countdown_seconds,
    )


@router.put("/api/system/tls/settings", response_model=TLSSettingsUpdateResponse)
async def update_tls_settings(
    payload: TLSSettingsUpdateRequest,
    background_tasks: BackgroundTasks,
    _: UserRecord = Depends(perm_admin),
) -> TLSSettingsUpdateResponse:
    """Updates TLS auto-renewal toggle and certificate provisioning mode.

    When cert_mode changes, triggers immediate re-provisioning of the selected type.
    """
    changed = False
    mode_changed = False
    if payload.auto_renew is not None and payload.auto_renew != settings.ssl_auto_renew:
        settings.ssl_auto_renew = payload.auto_renew
        changed = True

    if payload.cert_mode is not None and payload.cert_mode != settings.ssl_cert_mode:
        settings.ssl_cert_mode = payload.cert_mode
        changed = True
        mode_changed = True

    if changed:
        reload_settings()

    if mode_changed and payload.cert_mode is not None and payload.cert_mode != "custom":
        token = settings.duckdns_token or "" if payload.cert_mode == "letsencrypt" else ""
        result = await provision_tls_certificates(
            domain=settings.duckdns_domain or "localhost",
            token=token,
            force=True,
        )
        if result.success:
            background_tasks.add_task(_delayed_manager_restart)
            return TLSSettingsUpdateResponse(
                status="success",
                message=(
                    f"Certificate mode changed to '{payload.cert_mode}'. "
                    f"New {result.mode.value} certificate provisioned. Service restarting."
                ),
                auto_renew=settings.ssl_auto_renew,
                cert_mode=settings.ssl_cert_mode,
                canonical_url=_resolve_canonical_url(),
            )
        return TLSSettingsUpdateResponse(
            status="error",
            message=f"Failed to provision certificate for mode '{payload.cert_mode}': {result.error}",
            auto_renew=settings.ssl_auto_renew,
            cert_mode=settings.ssl_cert_mode,
            canonical_url=_resolve_canonical_url(),
        )

    return TLSSettingsUpdateResponse(
        status="success",
        message="TLS settings updated successfully.",
        auto_renew=settings.ssl_auto_renew,
        cert_mode=settings.ssl_cert_mode,
        canonical_url=_resolve_canonical_url(),
    )


def dispatch_manager_service_restart() -> None:
    """Dispatches a non-blocking systemctl restart of palworld-manager.service via supervisor."""
    if os.name == "posix":
        try:
            from app.supervisor.client import SupervisorClient  # pylint: disable=import-outside-toplevel
            client = SupervisorClient()
            asyncio.get_event_loop().create_task(client.restart_service("palworld-manager.service"))
            log.info("Dispatched supervisor IPC restart for palworld-manager.service")
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.error("Failed to dispatch manager service restart via supervisor: %s", err)
    else:
        log.info("Non-posix environment detected; skipping manager service restart.")


async def _delayed_manager_restart() -> None:
    """Allows HTTP response to flush cleanly before triggering service restart."""
    await asyncio.sleep(1.0)
    dispatch_manager_service_restart()


@router.post("/api/system/tls/renew", response_model=TLSRenewResponse)
async def trigger_tls_renewal(
    payload: TLSRenewRequest,
    background_tasks: BackgroundTasks,
    user: UserRecord = Depends(perm_admin),
) -> TLSRenewResponse:
    """Triggers an on-demand TLS certificate renewal.

    Restricted strictly to administrators.
    Supports early renewal via payload.force=True.

    Args:
        payload (TLSRenewRequest): Renewal trigger parameters.
        background_tasks (BackgroundTasks): FastAPI background task manager.
        user (UserRecord): Authenticated administrator triggering the action.

    Returns:
        TLSRenewResponse: Outcome status and informational message.
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log.info(
        "Administrator '%s' requested TLS certificate renewal (force=%s) at %s",
        user.username,
        payload.force,
        timestamp,
    )

    result = await provision_tls_certificates(
        domain=settings.duckdns_domain,
        token=settings.duckdns_token,
        force=payload.force,
    )
    if result.success:
        background_tasks.add_task(_delayed_manager_restart)
        msg = f"{result.message} Web management service is restarting to activate HTTPS."
        return TLSRenewResponse(
            status="success",
            message=msg,
            error_detail=None,
            canonical_url=_resolve_canonical_url(),
            triggered_at=timestamp,
        )
    return TLSRenewResponse(
        status="error",
        message=result.message,
        error_detail=result.error,
        canonical_url=_resolve_canonical_url(),
        triggered_at=timestamp,
    )
