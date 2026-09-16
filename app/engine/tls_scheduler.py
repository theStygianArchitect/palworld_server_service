"""In-process TLS certificate renewal scheduler and background provisioning tasks.

Executes periodic certificate expiration checks, automatic renewals, and DuckDNS
synchronization in compliance with Google Style Guide and 3 AM standards.
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings, resolve_ssl_paths
from app.core.logger import log
from app.routers.system import dispatch_manager_service_restart

# Resilient import: prevents startup failure if cryptography is absent
_TLS_ENGINE_AVAILABLE: bool = True  # pylint: disable=invalid-name
try:
    from app.engine.duckdns import sync_duckdns_ip
    from app.engine.tls_manager import get_tls_certificate_status, provision_tls_certificates
except ImportError as _exc:
    _TLS_ENGINE_AVAILABLE = False  # pylint: disable=invalid-name
    log.warning("TLS Engine dependencies unavailable (%s). Running in degraded mode.", _exc)
    sync_duckdns_ip = None  # type: ignore[assignment]
    get_tls_certificate_status = None  # type: ignore[assignment]
    provision_tls_certificates = None  # type: ignore[assignment]


async def trigger_duckdns_sync() -> None:
    """Invokes DuckDNS dynamic DNS updater on startup if available on the host."""
    if not _TLS_ENGINE_AVAILABLE:
        log.warning("DuckDNS sync skipped: TLS engine dependencies unavailable")
        return

    cfg = get_settings()
    if not cfg.duckdns_domain or not cfg.duckdns_token:
        return

    log.info("Triggering DuckDNS dynamic DNS synchronization directly via Python httpx")
    success = await sync_duckdns_ip(
        domain=cfg.duckdns_domain,
        token=cfg.duckdns_token,
    )
    if success:
        log.info("DuckDNS synchronization complete")
    else:
        log.warning("DuckDNS sync failed")


async def trigger_tls_provisioning_check() -> None:
    """Checks TLS certificate availability on startup and initiates provisioning if absent."""
    cfg = get_settings()
    if resolve_ssl_paths(cfg) is not None:
        return
    if not _TLS_ENGINE_AVAILABLE:
        log.warning("TLS provisioning check skipped: TLS engine dependencies unavailable")
        return

    raw_domain = str(cfg.duckdns_domain or "").strip().lower()
    if not raw_domain or raw_domain in ("localhost", "yourdomain.duckdns.org"):
        return

    log.info("TLS certificate missing on startup. Dispatching cert provisioning via native Python engine")
    result = await provision_tls_certificates(
        domain=cfg.duckdns_domain,
        token=cfg.duckdns_token,
        force=False,
    )
    if result.success:
        log.info("TLS certificate provisioning completed: %s. Reloading service.", result.message)
        dispatch_manager_service_restart()
    else:
        log.warning("TLS certificate provisioning failed: %s (Error: %s)", result.message, result.error)


async def certificate_renewal_scheduler() -> None:
    """Background scheduler that renews certificates based on expiry countdown.

    Respects ssl_auto_renew toggle and ssl_cert_mode selector. The expiry
    countdown is always tracked regardless of toggle state; only the renewal
    action is gated by the toggle.
    """
    if not _TLS_ENGINE_AVAILABLE:
        log.warning("TLS certificate renewal scheduler disabled: engine dependencies unavailable.")
        return

    while True:
        try:
            cfg = get_settings()

            # Custom mode: operator manages their own certs — skip renewal
            if cfg.ssl_cert_mode == "custom":
                log.info("Certificate mode is 'custom'. Auto-renewal disabled; monitoring expiry only.")
                await asyncio.sleep(86400)
                continue

            # Auto-renew toggle is off: sleep and re-check periodically
            if not cfg.ssl_auto_renew:
                log.info("Auto-renewal is disabled by operator. Sleeping 1 hour before re-checking.")
                await asyncio.sleep(3600)
                continue

            status = get_tls_certificate_status(domain=cfg.duckdns_domain or "localhost")
            if status.is_valid and status.days_remaining > 30:
                seconds_until_renewal = (status.days_remaining - 30) * 86400
                log.info(
                    "Certificate valid for %d days. Next renewal in %d days.",
                    status.days_remaining,
                    status.days_remaining - 30,
                )
                await asyncio.sleep(min(max(seconds_until_renewal, 3600), 86400))
                continue

            # Provision based on selected cert mode
            token = cfg.duckdns_token or "" if cfg.ssl_cert_mode == "letsencrypt" else ""
            result = await provision_tls_certificates(
                domain=cfg.duckdns_domain or "localhost",
                token=token,
                force=False,
            )
            if result.success:
                log.info(
                    "Certificate renewed (%s): %s. Dispatching service restart.",
                    cfg.ssl_cert_mode,
                    result.message,
                )
                dispatch_manager_service_restart()
                await asyncio.sleep(86400)
            else:
                log.warning("Certificate renewal failed: %s. Retrying in 6 hours.", result.error)
                await asyncio.sleep(21600)
        except asyncio.CancelledError:
            log.debug("Certificate renewal scheduler cancelled.")
            raise
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.exception("Certificate renewal scheduler error: %s", err)
            await asyncio.sleep(3600)


__all__ = [
    "_TLS_ENGINE_AVAILABLE",
    "certificate_renewal_scheduler",
    "trigger_duckdns_sync",
    "trigger_tls_provisioning_check",
]
