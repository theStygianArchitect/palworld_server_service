"""Integration tests for TLS status and certificate renewal REST API endpoints."""
# pylint: disable=redefined-outer-name
# Rationale: Pytest dependency injection requires test parameters to match fixture names.

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.database.models import UserRecord
from app.engine.tls_manager import TLSCertificateStatus, TLSProvisionMode, TLSProvisionResult
from app.main import app, db, get_current_user, trigger_tls_provisioning_check
from app.routers.system import dispatch_manager_service_restart


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Provides a FastAPI TestClient fixture with initialized database and admin session."""
    db.initialize()
    dummy_digest = "mock_admin_digest"
    admin_user = UserRecord(
        id=1,
        username="admin",
        password_hash=dummy_digest,
        salt=dummy_digest,
        email="admin@test.local",
        role="admin",
        is_active=True,
        created_at="2026-09-09T00:00:00Z",
    )
    app.dependency_overrides[get_current_user] = lambda: admin_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_get_tls_status_http_fallback(client: TestClient) -> None:
    """Tests GET /api/system/tls/status returns HTTP fallback when no certs exist."""
    mock_status = TLSCertificateStatus(
        is_valid=False,
        domain="localhost",
        issuer="",
        subject_alt_names=[],
        not_before=None,
        not_after=None,
        days_remaining=0,
        fullchain_path=None,
        privkey_path=None,
        is_self_signed=False,
        error_message="Mock error",
    )
    with patch("app.routers.system.get_tls_certificate_status", return_value=mock_status):
        resp = client.get("/api/system/tls/status")
        assert resp.status_code == 200
        data = resp.json()
        assert not data["enabled"]
        assert data["scheme"] == "http"
        assert data["certificate"] is None
        assert "warning" in data
        assert "canonical_url" in data
        assert data["canonical_url"] == "https://thestygianarchitect.duckdns.org:8080"


def test_get_tls_status_https_active(client: TestClient, tmp_path: Path) -> None:
    """Tests GET /api/system/tls/status returns HTTPS active with certificate metadata."""
    cert_path = tmp_path / "fullchain.pem"
    key_path = tmp_path / "privkey.pem"
    cert_path.touch()
    key_path.touch()

    mock_status = TLSCertificateStatus(
        is_valid=True,
        domain="api-gateway.duckdns.org",
        issuer="Let's Encrypt Authority R3",
        subject_alt_names=["api-gateway.duckdns.org", "palworld.duckdns.org"],
        not_before=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        not_after=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
        days_remaining=67,
        fullchain_path=cert_path,
        privkey_path=key_path,
        is_self_signed=False,
    )

    with patch("app.routers.system.get_tls_certificate_status", return_value=mock_status):
        resp = client.get("/api/system/tls/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["scheme"] == "https"
        assert data["certificate"]["subject"] == "api-gateway.duckdns.org"
        assert data["certificate"]["days_remaining"] == 67
        assert data["cert_path"] == str(cert_path)
        assert "canonical_url" in data
        assert data["canonical_url"] == "https://thestygianarchitect.duckdns.org:8080"


def test_post_tls_renew_failure_handling(client: TestClient) -> None:
    """Tests POST /api/system/tls/renew handles provisioning failure gracefully."""
    mock_result = TLSProvisionResult(
        success=False,
        mode=TLSProvisionMode.ACME_LETSENCRYPT,
        domain="localhost",
        fullchain_path=Path(),
        privkey_path=Path(),
        days_remaining=0,
        message="Provisioning failed",
        error="DuckDNS token invalid",
    )
    with patch("app.routers.system.provision_tls_certificates", return_value=mock_result):
        resp = client.post("/api/system/tls/renew", json={"force": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert data["message"] == "Provisioning failed"
        assert data["error_detail"] == "DuckDNS token invalid"


def test_post_tls_renew_success(client: TestClient, tmp_path: Path) -> None:
    """Tests POST /api/system/tls/renew executes renewal script successfully and schedules service restart."""
    mock_result = TLSProvisionResult(
        success=True,
        mode=TLSProvisionMode.ACME_LETSENCRYPT,
        domain="localhost",
        fullchain_path=tmp_path / "fullchain.pem",
        privkey_path=tmp_path / "privkey.pem",
        days_remaining=89,
        message="Certificate renewed successfully",
        error=None,
    )
    with patch("app.routers.system.provision_tls_certificates", return_value=mock_result) as mock_prov, \
         patch("app.routers.system._delayed_manager_restart", new_callable=AsyncMock) as mock_restart:
        resp = client.post("/api/system/tls/renew", json={"force": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "Certificate renewed successfully" in data["message"]
        assert "Web management service is restarting" in data["message"]

        mock_prov.assert_called_once()
        assert mock_prov.call_args[1].get("force") is True
        mock_restart.assert_called_once()


def test_dispatch_manager_service_restart_posix() -> None:
    """Tests dispatch_manager_service_restart delegates to SupervisorClient on POSIX."""
    mock_client_instance = AsyncMock()
    mock_client_class = MagicMock(return_value=mock_client_instance)

    with patch("app.routers.system.os.name", "posix"), \
         patch("app.supervisor.client.SupervisorClient", mock_client_class):

        # We need an event loop to create the task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            dispatch_manager_service_restart()
            # Run the event loop to execute the create_task callback
            loop.run_until_complete(asyncio.sleep(0.01))
        finally:
            loop.close()

        mock_client_instance.restart_service.assert_called_once_with("palworld-manager.service")


def test_dispatch_manager_service_restart_non_posix() -> None:
    """Tests dispatch_manager_service_restart is a no-op on non-POSIX systems."""
    with patch("app.routers.system.os.name", "nt"), \
         patch("app.routers.system.log.info") as mock_log:
        dispatch_manager_service_restart()
        mock_log.assert_called_with("Non-posix environment detected; skipping manager service restart.")


def test_dispatch_manager_service_restart_handles_exception() -> None:
    """Tests dispatch_manager_service_restart catches and logs exceptions gracefully."""
    mock_client_class = MagicMock(side_effect=RuntimeError("IPC failed"))

    with patch("app.routers.system.os.name", "posix"), \
         patch("app.supervisor.client.SupervisorClient", mock_client_class), \
         patch("app.routers.system.log.error") as mock_log:
        dispatch_manager_service_restart()
        mock_log.assert_called_once()
        assert "Failed to dispatch manager service restart via supervisor" in mock_log.call_args[0][0]


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


def test_post_tls_renew_unhandled_exception(client: TestClient) -> None:
    """Test POST /api/system/tls/renew surfaces 500 when provision_tls_certificates raises an error."""
    with patch("app.routers.system.provision_tls_certificates", side_effect=RuntimeError("Unhandled error")):
        with pytest.raises(RuntimeError) as exc_info:
            client.post("/api/system/tls/renew", json={"force": False})
        assert "Unhandled error" in str(exc_info.value)


def test_canonical_redirect_endpoint_redirects_http_to_https(client: TestClient) -> None:
    """Regression test for Issue #40: GET /canonical issues HTTP 307 redirect to canonical HTTPS portal."""
    resp = client.get("/canonical", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://thestygianarchitect.duckdns.org:8080/"


def test_canonical_redirect_honors_configured_domain(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for Issue #40: GET /canonical honors custom duckdns_domain configuration."""
    monkeypatch.setattr("app.main.settings.duckdns_domain", "custom-server.duckdns.org")
    resp = client.get("/canonical", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://custom-server.duckdns.org:8080/"


def test_canonical_url_exposed_in_system_version(client: TestClient) -> None:
    """Regression test for Issue #40: GET /api/system/version returns canonical_url."""
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
        resp = client.get("/api/system/version")
        assert resp.status_code == 200
        data = resp.json()
        assert "canonical_url" in data
        assert data["canonical_url"].startswith("https://")
        assert "thestygianarchitect.duckdns.org:8080" in data["canonical_url"]
    finally:
        app.dependency_overrides.clear()


def test_canonical_url_exposed_in_tls_status(client: TestClient) -> None:
    """Regression test for Issue #40: GET /api/system/tls/status includes canonical_url in both HTTP and HTTPS modes."""
    # HTTP fallback mode
    mock_status_http = TLSCertificateStatus(
        is_valid=False,
        domain="localhost",
        issuer="",
        subject_alt_names=[],
        not_before=None,
        not_after=None,
        days_remaining=0,
        fullchain_path=None,
        privkey_path=None,
        is_self_signed=False,
    )
    with patch("app.routers.system.get_tls_certificate_status", return_value=mock_status_http):
        resp_http = client.get("/api/system/tls/status")
        assert resp_http.status_code == 200
        data_http = resp_http.json()
        assert "canonical_url" in data_http
        assert data_http["canonical_url"] == "https://thestygianarchitect.duckdns.org:8080"

    # HTTPS active mode
    mock_cert = Path("/mock/cert.pem")
    mock_key = Path("/mock/key.pem")
    mock_status_https = TLSCertificateStatus(
        is_valid=True,
        domain="localhost",
        issuer="",
        subject_alt_names=[],
        not_before=None,
        not_after=None,
        days_remaining=10,
        fullchain_path=mock_cert,
        privkey_path=mock_key,
        is_self_signed=False,
    )
    with patch("app.routers.system.get_tls_certificate_status", return_value=mock_status_https):
        resp_https = client.get("/api/system/tls/status")
        assert resp_https.status_code == 200
        data_https = resp_https.json()
        assert "canonical_url" in data_https
        assert data_https["canonical_url"] == "https://thestygianarchitect.duckdns.org:8080"


def test_canonical_redirect_location_contract() -> None:
    """Regression test for Issue #40: GET /canonical contract asserts HTTP 307 and canonical HTTPS target."""
    non_auth_client = TestClient(app, base_url="https://thestygianarchitect.duckdns.org:8080")
    resp = non_auth_client.get("/canonical", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://thestygianarchitect.duckdns.org:8080/"


def test_canonical_navigation_end_to_end_unauthenticated_flow() -> None:
    """Regression test for Issue #40: Unauthenticated client navigating to canonical URL lands on /login?next=/."""
    non_auth_client = TestClient(app, base_url="https://thestygianarchitect.duckdns.org:8080")
    remote_headers = {"X-Forwarded-For": "198.51.100.1"}
    resp = non_auth_client.get("/canonical", headers=remote_headers, follow_redirects=True)
    assert resp.status_code == 200
    assert str(resp.url).endswith("/login?next=/")
    assert "Sign In" in resp.text


def test_canonical_navigation_end_to_end_authenticated_flow(client: TestClient) -> None:
    """Regression test for Issue #40: Authenticated client navigating to canonical URL lands on dashboard."""
    resp = client.get("https://thestygianarchitect.duckdns.org:8080/canonical", follow_redirects=True)
    assert resp.status_code == 200
    assert str(resp.url) == "https://thestygianarchitect.duckdns.org:8080/"
    assert "Palworld Server Operations Suite" in resp.text


@pytest.mark.asyncio
async def test_tls_auto_provision_startup_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for Issue #40: Startup hook dispatches cert manager renew when certs absent."""
    monkeypatch.setattr("app.main.settings.duckdns_domain", "thestygianarchitect.duckdns.org")
    monkeypatch.setattr("app.main.settings.duckdns_token", "test-token")

    with (
        patch("app.main.resolve_ssl_paths", return_value=None),
        patch("app.main.provision_tls_certificates", new_callable=AsyncMock) as mock_prov,
    ):
        await trigger_tls_provisioning_check()
        mock_prov.assert_called_once_with(
            domain="thestygianarchitect.duckdns.org",
            token="test-token",  # nosec B106 - mock test token
            force=False,
        )
