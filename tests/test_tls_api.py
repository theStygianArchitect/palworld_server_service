"""Integration tests for TLS status and certificate renewal REST API endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import TLSCertificateInfo
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provides a TestClient for app testing."""
    return TestClient(app)


def test_get_tls_status_http_fallback(client: TestClient) -> None:
    """Tests GET /api/system/tls/status returns HTTP fallback when no certs exist."""
    with patch("app.main.resolve_ssl_paths", return_value=None):
        resp = client.get("/api/system/tls/status")
        assert resp.status_code == 200
        data = resp.json()
        assert not data["enabled"]
        assert data["scheme"] == "http"
        assert data["certificate"] is None
        assert "warning" in data


def test_get_tls_status_https_active(client: TestClient, tmp_path: Path) -> None:
    """Tests GET /api/system/tls/status returns HTTPS active with certificate metadata."""
    cert_path = tmp_path / "fullchain.pem"
    key_path = tmp_path / "privkey.pem"
    cert_path.touch()
    key_path.touch()

    mock_cert_info = TLSCertificateInfo(
        subject="myserver.duckdns.org",
        issuer="Let's Encrypt",
        valid_from="2026-09-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z",
        days_remaining=83,
        is_expired=False,
        san_list=["myserver.duckdns.org"],
    )

    with (
        patch("app.main.resolve_ssl_paths", return_value=(cert_path, key_path)),
        patch("app.main.inspect_certificate", return_value=mock_cert_info),
    ):
        resp = client.get("/api/system/tls/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["scheme"] == "https"
        assert data["certificate"]["subject"] == "myserver.duckdns.org"
        assert data["certificate"]["days_remaining"] == 83
        assert data["cert_path"] == str(cert_path)


def test_post_tls_renew_skipped_when_script_absent(client: TestClient) -> None:
    """Tests POST /api/system/tls/renew handles absent palworld-cert-manager.sh gracefully."""
    with patch("pathlib.Path.is_file", return_value=False):
        resp = client.post("/api/system/tls/renew", json={"force": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert "not installed" in data["message"]


def test_post_tls_renew_success(client: TestClient) -> None:
    """Tests POST /api/system/tls/renew executes renewal script successfully."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Certificate renewed successfully"
    mock_proc.stderr = ""

    with (
        patch("pathlib.Path.is_file", return_value=True),
        patch("subprocess.run", return_value=mock_proc) as mock_run,
    ):
        resp = client.post("/api/system/tls/renew", json={"force": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "Certificate renewed successfully" in data["message"]

        # Verify --force argument was included
        call_args = mock_run.call_args[0][0]
        assert "--force" in call_args
