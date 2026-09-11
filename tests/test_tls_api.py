"""Integration tests for TLS status and certificate renewal REST API endpoints."""
# pylint: disable=redefined-outer-name
# Rationale: Pytest dependency injection requires test parameters to match fixture names.

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import TLSCertificateInfo
from app.database.models import UserRecord
from app.main import app, get_current_user


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
        subject="api-gateway.duckdns.org",
        issuer="Let's Encrypt Authority R3",
        valid_from="2026-08-15T12:00:00Z",
        expires_at="2026-11-15T12:00:00Z",
        days_remaining=67,
        is_expired=False,
        san_list=["api-gateway.duckdns.org", "palworld.duckdns.org"],
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
        assert data["certificate"]["subject"] == "api-gateway.duckdns.org"
        assert data["certificate"]["days_remaining"] == 67
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


def test_tls_endpoints_forbidden_for_viewer(client: TestClient) -> None:
    """Test GET /api/system/tls/status and POST /api/system/tls/renew return 403 for viewer.

    Regression test for Issue #28: Sudo password prompt remediation on cert renewal and admin-only certificate RBAC.
    """
    dummy_digest = str(id(client))
    viewer_user = UserRecord(
        id=99,
        username="test_viewer",
        password_hash=dummy_digest,
        salt=dummy_digest,
        email="viewer@test.local",
        role="viewer",
        is_active=True,
        created_at="2026-09-09T00:00:00Z",
    )
    app.dependency_overrides[get_current_user] = lambda: viewer_user
    try:
        resp_status = client.get("/api/system/tls/status")
        assert resp_status.status_code == 403
        assert "Administrator role required" in resp_status.json()["detail"]

        resp_renew = client.post("/api/system/tls/renew", json={"force": False})
        assert resp_renew.status_code == 403
        assert "Administrator role required" in resp_renew.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_tls_endpoints_forbidden_for_operator(client: TestClient) -> None:
    """Test GET /api/system/tls/status and POST /api/system/tls/renew return 403 for operator.

    Regression test for Issue #28: Sudo password prompt remediation on cert renewal and admin-only certificate RBAC.
    """
    dummy_digest = str(id(client))
    operator_user = UserRecord(
        id=98,
        username="test_operator",
        password_hash=dummy_digest,
        salt=dummy_digest,
        email="operator@test.local",
        role="operator",
        is_active=True,
        created_at="2026-09-09T00:00:00Z",
    )
    app.dependency_overrides[get_current_user] = lambda: operator_user
    try:
        resp_status = client.get("/api/system/tls/status")
        assert resp_status.status_code == 403
        assert "Administrator role required" in resp_status.json()["detail"]

        resp_renew = client.post("/api/system/tls/renew", json={"force": False})
        assert resp_renew.status_code == 403
        assert "Administrator role required" in resp_renew.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_post_tls_renew_sudo_password_remediation_error(client: TestClient) -> None:
    """Test POST /api/system/tls/renew surfaces actionable remediation when sudo requires password.

    Regression test for Issue #28: Sudo password prompt remediation on cert renewal and admin-only certificate RBAC.
    """
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = (
        "sudo: a terminal is required to read the password; either use the -S option to read from standard input\n"
        "sudo: a password is required"
    )

    with (
        patch("pathlib.Path.is_file", return_value=True),
        patch("subprocess.run", return_value=mock_proc),
    ):
        resp = client.post("/api/system/tls/renew", json={"force": False})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "passwordless sudo is not configured" in detail
        assert "palmanager-certs" in detail
