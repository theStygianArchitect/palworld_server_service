"""Integration tests for the PUT /api/system/tls/settings endpoint and status enhancements."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,duplicate-code

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from app.database.models import UserRecord
from app.engine.tls_manager import TLSProvisionMode, TLSProvisionResult
from app.main import app, db, get_current_user


@pytest.fixture
def admin_client() -> Generator[TestClient, None, None]:
    """Provides a FastAPI TestClient fixture with initialized database and admin session."""
    db.initialize()
    admin_user = UserRecord(
        id=1,
        username="admin",
        password_hash="mock_hash",  # nosec B106 - mock test hash
        salt="mock_salt",
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


@pytest.fixture
def viewer_client() -> Generator[TestClient, None, None]:
    """Provides a FastAPI TestClient fixture with a non-admin viewer session."""
    db.initialize()
    viewer_user = UserRecord(
        id=2,
        username="viewer",
        password_hash="mock_hash",  # nosec B106 - mock test hash
        salt="mock_salt",
        email="viewer@test.local",
        role="viewer",
        is_active=True,
        created_at="2026-09-09T00:00:00Z",
    )
    app.dependency_overrides[get_current_user] = lambda: viewer_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_update_tls_settings_toggle_auto_renew(admin_client: TestClient) -> None:
    """Verifies that PUT /api/system/tls/settings can toggle auto_renew on and off."""
    resp = admin_client.put(
        "/api/system/tls/settings",
        json={"auto_renew": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["auto_renew"] is True

    # Toggle back to False
    resp2 = admin_client.put(
        "/api/system/tls/settings",
        json={"auto_renew": False},
    )
    assert resp2.status_code == 200
    assert resp2.json()["auto_renew"] is False


def test_update_tls_settings_change_mode(admin_client: TestClient) -> None:
    """Verifies that changing cert_mode triggers immediate re-provisioning and scheduled restart."""
    fake_result = TLSProvisionResult(
        success=True,
        mode=TLSProvisionMode.SELF_SIGNED_FALLBACK,
        domain="localhost",
        fullchain_path=None,
        privkey_path=None,
        days_remaining=365,
        message="Self-signed cert provisioned",
    )
    prov_mock = AsyncMock(return_value=fake_result)
    with (
        patch("app.routers.system.provision_tls_certificates", prov_mock),
        patch("app.routers.system._delayed_manager_restart") as mock_restart,
    ):

        resp = admin_client.put(
            "/api/system/tls/settings",
            json={"cert_mode": "self_signed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["cert_mode"] == "self_signed"
        prov_mock.assert_called_once()
        mock_restart.assert_called_once()


def test_update_tls_settings_rejects_invalid_mode(admin_client: TestClient) -> None:
    """Verifies that unrecognized cert_mode values fail schema validation with HTTP 422."""
    resp = admin_client.put(
        "/api/system/tls/settings",
        json={"cert_mode": "unsupported_mode"},
    )
    assert resp.status_code == 422


def test_update_tls_settings_rbac_enforcement(viewer_client: TestClient) -> None:
    """Verifies that non-admin users cannot mutate TLS settings (HTTP 403)."""
    resp = viewer_client.put(
        "/api/system/tls/settings",
        json={"auto_renew": True},
    )
    assert resp.status_code == 403


def test_get_tls_status_includes_countdown_and_mode_fields(admin_client: TestClient) -> None:
    """Verifies that GET /api/system/tls/status returns cert_mode and countdown fields."""
    resp = admin_client.get("/api/system/tls/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "cert_mode" in data
    assert "auto_renew_active" in data
    assert "next_renewal_at" in data
    assert "renewal_countdown_seconds" in data
