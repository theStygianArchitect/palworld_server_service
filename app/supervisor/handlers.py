"""Async handler functions for supervisor RPC methods."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.supervisor.protocol import (
    ServiceReloadParams,
    ServiceRestartParams,
    ServiceStatusParams,
    SystemDeployParams,
    SystemRebootParams,
)

logger = logging.getLogger(__name__)


async def handle_service_restart(params: ServiceRestartParams) -> dict[str, Any]:
    """Restart a systemd service.

    Args:
        params: Validated parameters containing service name and timeout.

    Returns:
        A dictionary with the success or error status.
    """
    if os.name != "posix":
        return {"status": "skipped", "reason": "non-posix"}

    service_name = params.service_name
    logger.info("Restarting service %s with timeout %ds", service_name, params.timeout_seconds)

    process = await asyncio.create_subprocess_exec(
        "/bin/systemctl",
        "restart",
        service_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=params.timeout_seconds)
    except asyncio.TimeoutError:
        with contextlib.suppress(OSError):
            process.kill()
        return {"status": "error", "service": service_name, "reason": "timeout"}

    if process.returncode != 0:
        return {
            "status": "error",
            "service": service_name,
            "returncode": process.returncode,
            "stderr": stderr.decode(errors="replace").strip() if stderr else "",
        }

    return {"status": "success", "service": service_name, "action": "restart"}


async def handle_service_status(params: ServiceStatusParams) -> dict[str, Any]:
    """Check the status of a systemd service.

    Args:
        params: Validated parameters containing the service name.

    Returns:
        A dictionary with the service name and active state.
    """
    if os.name != "posix":
        return {"status": "skipped", "reason": "non-posix"}

    service_name = params.service_name
    logger.info("Querying status for service %s", service_name)

    process = await asyncio.create_subprocess_exec(
        "/bin/systemctl",
        "is-active",
        service_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    return {"service": service_name, "active_state": stdout.decode(errors="replace").strip()}


async def handle_service_reload(params: ServiceReloadParams) -> dict[str, Any]:
    """Reload the systemd daemon or service.

    Args:
        params: Validated parameters indicating whether to perform a daemon-reload.

    Returns:
        A dictionary with the success status.
    """
    if os.name != "posix":
        return {"status": "skipped", "reason": "non-posix"}

    if params.daemon_reload:
        logger.info("Executing systemctl daemon-reload")
        process = await asyncio.create_subprocess_exec(
            "/bin/systemctl",
            "daemon-reload",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        return {"status": "success", "action": "daemon-reload"}

    return {"status": "success", "action": "none"}


async def handle_system_reboot(params: SystemRebootParams) -> dict[str, Any]:
    """Schedule or execute a system reboot.

    Args:
        params: Validated parameters containing delay and reason.

    Returns:
        A dictionary with the scheduled status and parameters.
    """
    if os.name != "posix":
        return {"status": "skipped", "reason": "non-posix"}

    delay_seconds = params.delay_seconds
    reason = params.reason

    logger.info("Scheduling reboot in %d seconds. Reason: %s", delay_seconds, reason)

    if delay_seconds > 0:
        minutes = max(1, delay_seconds // 60)
        process = await asyncio.create_subprocess_exec(
            "/sbin/shutdown",
            "-r",
            f"+{minutes}",
            reason,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            "/sbin/shutdown",
            "-r",
            "now",
            reason,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    await process.communicate()
    return {"status": "scheduled", "delay_seconds": delay_seconds, "reason": reason}


async def handle_system_deploy(params: SystemDeployParams) -> dict[str, Any]:
    """Execute a system deployment script in a detached process.

    Args:
        params: Validated parameters containing the target branch.

    Returns:
        A dictionary with the dispatched status and PID.
    """
    if os.name != "posix":
        return {"status": "skipped", "reason": "non-posix"}

    target_branch = params.target_branch
    logger.info("Executing deploy runner for branch %s", target_branch)

    process = await asyncio.create_subprocess_exec(
        "/var/lib/palmanager/deploy-runner.sh",
        target_branch,
        start_new_session=True,
    )
    return {"status": "dispatched", "target_branch": target_branch, "pid": process.pid}


METHOD_DISPATCH: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "service.restart": handle_service_restart,
    "service.status": handle_service_status,
    "service.reload": handle_service_reload,
    "system.reboot": handle_system_reboot,
    "system.deploy": handle_system_deploy,
}
