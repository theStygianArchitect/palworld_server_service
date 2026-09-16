"""Regression tests for scheme-aware canonical URL resolution and /canonical redirect."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from app.core.config import AppSettings
from app.engine.tls_manager import (
    certificate_to_pem,
    generate_private_key,
    generate_self_signed_certificate,
    private_key_to_pem,
)
from app.main import app
from app.routers.system import _resolve_canonical_url


@pytest.fixture
def dummy_cert_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Generates dummy matching cert and key files on disk."""
    key = generate_private_key()
    cert = generate_self_signed_certificate(key, "localhost")
    cert_path = tmp_path / "fullchain.pem"
    key_path = tmp_path / "privkey.pem"
    cert_path.write_bytes(certificate_to_pem(cert))
    key_path.write_bytes(private_key_to_pem(key))
    return cert_path, key_path


def test_resolve_canonical_url_defaults() -> None:
    """Verifies that _resolve_canonical_url returns canonical https URL with default domain and port."""
    url = _resolve_canonical_url()
    assert url.startswith("https://")
    assert "8080" in url or f":{AppSettings().web_port}" in url


def test_resolve_canonical_url_honors_configured_domain() -> None:
    """Verifies that _resolve_canonical_url respects custom configured duckdns domain."""
    with patch("app.routers.system.settings.duckdns_domain", "custom.duckdns.org"):
        url = _resolve_canonical_url()
        assert url == f"https://custom.duckdns.org:{AppSettings().web_port}"


def test_canonical_redirect_endpoint_targets_canonical_https() -> None:
    """Verifies that /canonical 307 redirect targets the canonical HTTPS origin."""
    client = TestClient(app, follow_redirects=False)
    res = client.get("/canonical")
    assert res.status_code == 307
    target = res.headers["location"]
    assert target.startswith("https://")
    assert target.endswith("/")
