"""Palworld Operations Suite Uvicorn Server Entrypoint.

Provides native Python web server launcher with automated self-signed TLS bootstrapping
to prevent plaintext socket trapping on first-boot.
"""

from __future__ import annotations

from typing import Any

import uvicorn

from app.core.config import get_settings, resolve_ssl_paths
from app.core.logger import log
from app.engine.tls_scheduler import _TLS_ENGINE_AVAILABLE

try:
    from app.engine.tls_manager import (
        certificate_to_pem,
        generate_private_key,
        generate_self_signed_certificate,
        private_key_to_pem,
        stage_tls_bundle,
    )
except ImportError as err:
    log.debug("TLS manager dependencies unavailable for bootstrap: %s", err)
    certificate_to_pem = None  # type: ignore[assignment]
    generate_private_key = None  # type: ignore[assignment]
    generate_self_signed_certificate = None  # type: ignore[assignment]
    private_key_to_pem = None  # type: ignore[assignment]
    stage_tls_bundle = None  # type: ignore[assignment]


def run_server() -> None:
    """Entrypoint to launch Uvicorn web server with native TLS bootstrapping.

    If SSL is configured/enabled but no valid certificate files exist on disk,
    generates a fast bootstrap self-signed certificate so Uvicorn starts with
    active HTTPS encryption immediately on the designated web port.
    """
    cfg = get_settings()
    ssl_paths = resolve_ssl_paths(cfg)

    # If no SSL paths resolved and TLS engine is available, bootstrap self-signed cert
    if ssl_paths is None and _TLS_ENGINE_AVAILABLE and generate_private_key is not None:
        try:
            key = generate_private_key()
            domain = cfg.duckdns_domain or "localhost"
            cert = generate_self_signed_certificate(key, domain)
            stage_tls_bundle(certificate_to_pem(cert), private_key_to_pem(key))
            ssl_paths = resolve_ssl_paths(cfg)
            log.info("Bootstrapped self-signed TLS certificate for %s", domain)
        except OSError as err:
            log.warning("Filesystem error bootstrapping self-signed certificate: %s", err)
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.warning("Unexpected error bootstrapping self-signed certificate: %s", err)

    ssl_kwargs: dict[str, Any] = {}
    if ssl_paths:
        cert_file, key_file = ssl_paths
        ssl_kwargs["ssl_certfile"] = str(cert_file)
        ssl_kwargs["ssl_keyfile"] = str(key_file)
        log.info("Starting web server with TLS on port %d (%s)", cfg.web_port, cert_file)
    else:
        log.info("Starting web server with plaintext HTTP on port %d", cfg.web_port)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # nosec B104 - binding to all interfaces for community server access
        port=cfg.web_port,
        reload=False,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    run_server()

__all__ = ["run_server"]
