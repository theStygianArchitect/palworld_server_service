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


def test_resolve_canonical_url_when_tls_inactive() -> None:
    """Verifies that _resolve_canonical_url returns an http:// URL when no TLS certs are loaded."""
    with patch("app.routers.system.resolve_ssl_paths", return_value=None):
        url = _resolve_canonical_url()
        assert url.startswith("http://")
        assert not url.startswith("https://")
        assert ":8080" in url or f":{AppSettings().web_port}" in url


def test_resolve_canonical_url_when_tls_active(dummy_cert_pair: tuple[Path, Path]) -> None:
    """Verifies that _resolve_canonical_url returns an https:// URL when valid TLS certs are present."""
    with patch("app.routers.system.resolve_ssl_paths", return_value=dummy_cert_pair):
        url = _resolve_canonical_url()
        assert url.startswith("https://")
        assert ":8080" in url or f":{AppSettings().web_port}" in url


def test_canonical_redirect_endpoint_matches_scheme(dummy_cert_pair: tuple[Path, Path]) -> None:
    """Verifies that the /canonical 307 redirect targets the scheme determined by TLS readiness."""
    client = TestClient(app, follow_redirects=False)

    # When TLS is inactive -> redirects to http://
    with patch("app.routers.system.resolve_ssl_paths", return_value=None):
        res_http = client.get("/canonical")
        assert res_http.status_code == 307
        target = res_http.headers["location"]
        assert target.startswith("http://")

    # When TLS is active -> redirects to https://
    with patch("app.routers.system.resolve_ssl_paths", return_value=dummy_cert_pair):
        res_https = client.get("/canonical")
        assert res_https.status_code == 307
        target = res_https.headers["location"]
        assert target.startswith("https://")
