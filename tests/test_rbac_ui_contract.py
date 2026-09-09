"""Contract and integration tests for RBAC UI gating, diagnostics, and update check."""
# pylint: disable=missing-function-docstring
# Rationale: Pytest test function names are self-descriptive and documented via assertions.
# pylint: disable=redefined-outer-name
# Rationale: Pytest dependency injection requires test parameters to match fixture names.

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import UpdateStatusResponse
from app.database.auth import bootstrap_admin_user
from app.main import app, db, metrics_db, settings


@pytest.fixture
def rbac_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Provides an isolated FastAPI TestClient fixture with clean DB and admin user."""
    orig_db = db.db_path
    orig_metrics = metrics_db.db_path
    orig_updater = settings.updater_enabled
    settings.updater_enabled = False
    db.close()
    db.db_path = str(tmp_path / "rbac.db")
    db.initialize()
    metrics_db.close()
    metrics_db.db_path = str(tmp_path / "rbac_metrics.db")
    metrics_db.initialize()
    bootstrap_admin_user(db=db, default_password=settings.AdminPassword)
    test_client = TestClient(app)
    try:
        with test_client:
            yield test_client
    finally:
        settings.updater_enabled = orig_updater
        db.close()
        db.db_path = orig_db
        db.initialize()
        metrics_db.close()
        metrics_db.db_path = orig_metrics
        metrics_db.initialize()


def _create_user_and_token(
    client: TestClient, username: str, password: str, role: str
) -> str:
    """Helper to create or promote a user to the specified role and return a bearer token."""
    # 1. Register public user (starts as viewer)
    reg_res = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "email": f"{username}@test.local"},
    )
    user_id = reg_res.json()["id"]

    # 2. Promote to target role if not viewer
    if role != "viewer":
        admin_login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": settings.AdminPassword},
        )
        admin_token = admin_login.json()["token"]
        client.patch(
            f"/api/users/{user_id}/role",
            json={"role": role},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    # 3. Login to receive session token with updated permissions
    login_res = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    token = str(login_res.json()["token"])
    client.cookies.clear()
    return token


def test_system_update_check_rbac(rbac_client: TestClient):
    pwd_val = f"Strong_{'Password'}_789!"
    viewer_token = _create_user_and_token(rbac_client, "viewer_update_test", pwd_val, "viewer")
    operator_token = _create_user_and_token(rbac_client, "operator_update_test", pwd_val, "operator")
    admin_login = rbac_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": settings.AdminPassword},
    )
    admin_token = admin_login.json()["token"]
    rbac_client.cookies.clear()

    non_local = {"X-Forwarded-For": "198.51.100.1"}

    # 1. Unauthenticated non-localhost request must be rejected (401)
    unauth_res = rbac_client.post("/api/system/update/check", headers=non_local)
    assert unauth_res.status_code == 401

    # 2. Viewer lacks system:update permission (403)
    viewer_headers = {"Authorization": f"Bearer {viewer_token}", **non_local}
    viewer_res = rbac_client.post("/api/system/update/check", headers=viewer_headers)
    assert viewer_res.status_code == 403

    mock_status = UpdateStatusResponse(
        update_available=False,
        current_commit="abc1234",
        latest_commit="abc1234",
        commits_behind=0,
        latest_commit_message="Initial commit",
        last_checked="2026-09-08T00:00:00Z",
        update_in_progress=False,
    )

    # 3. Operator has system:update permission (200)
    with patch("app.main.updater.check_for_updates", AsyncMock(return_value=mock_status)):
        op_headers = {"Authorization": f"Bearer {operator_token}", **non_local}
        op_res = rbac_client.post("/api/system/update/check", headers=op_headers)
        assert op_res.status_code == 200
        assert op_res.json()["current_commit"] == "abc1234"

    # 4. Admin has system:update permission (200)
    with patch("app.main.updater.check_for_updates", AsyncMock(return_value=mock_status)):
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        admin_res = rbac_client.post("/api/system/update/check", headers=admin_headers)
        assert admin_res.status_code == 200
        assert admin_res.json()["current_commit"] == "abc1234"


def test_rbac_permission_matrix_contract(rbac_client: TestClient):
    pwd_val = f"Secure_{'P@ssword'}_456!"
    viewer_token = _create_user_and_token(rbac_client, "matrix_viewer", pwd_val, "viewer")
    operator_token = _create_user_and_token(rbac_client, "matrix_operator", pwd_val, "operator")
    admin_login = rbac_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": settings.AdminPassword},
    )
    admin_token = admin_login.json()["token"]
    rbac_client.cookies.clear()

    non_local = {"X-Forwarded-For": "198.51.100.2"}
    viewer_headers = {"Authorization": f"Bearer {viewer_token}", **non_local}
    op_headers = {"Authorization": f"Bearer {operator_token}", **non_local}
    admin_headers = {"Authorization": f"Bearer {admin_token}", **non_local}

    # Viewer: Can view settings, but cannot modify
    assert rbac_client.get("/api/settings", headers=viewer_headers).status_code == 200
    assert rbac_client.post("/api/settings", json={"ExpRate": 1.5}, headers=viewer_headers).status_code == 403

    # Viewer: Can view tracker/community, but cannot kick or ban players
    assert rbac_client.get("/api/tracker/community", headers=viewer_headers).status_code == 200
    assert rbac_client.post(
        "/api/players/kick", json={"player_id": "p1"}, headers=viewer_headers
    ).status_code == 403
    assert rbac_client.post(
        "/api/players/ban", json={"player_id": "p1"}, headers=viewer_headers
    ).status_code == 403

    # Viewer: Can view and create feedback
    assert rbac_client.get("/api/feedback", headers=viewer_headers).status_code == 200
    fb_res = rbac_client.post(
        "/api/feedback",
        json={"title": "Viewer Feedback", "category": "feature_request", "body": "Great tool"},
        headers=viewer_headers,
    )
    assert fb_res.status_code == 200

    # Viewer: Cannot manage users or view audit logs
    assert rbac_client.get("/api/users", headers=viewer_headers).status_code == 403
    assert rbac_client.get("/api/auth/audit", headers=viewer_headers).status_code == 403

    # Operator: Can modify settings
    with (
        patch("app.main._write_ini_file_with_fallback", return_value=None),
        patch("app.main.reload_settings", return_value=None),
    ):
        op_settings = rbac_client.post("/api/settings", json={"ExpRate": 1.5}, headers=op_headers)
        assert op_settings.status_code == 200

    # Operator: Can kick players
    with patch("app.main.engine.kick_player", AsyncMock(return_value={"status": "success"})):
        op_kick = rbac_client.post("/api/players/kick", json={"player_id": "p1"}, headers=op_headers)
        assert op_kick.status_code == 200

    # Operator: Cannot manage users
    assert rbac_client.get("/api/users", headers=op_headers).status_code == 403

    # Admin: Full access including users and audit
    assert rbac_client.get("/api/users", headers=admin_headers).status_code == 200
    assert rbac_client.get("/api/auth/audit", headers=admin_headers).status_code == 200


def test_dashboard_ui_rbac_and_diagnostic_contract(rbac_client: TestClient):
    response = rbac_client.get("/")
    assert response.status_code == 200
    content = response.text

    # Verify System Updates UI tab and elements
    assert 'id="tab_updates"' in content
    assert 'id="page_updates"' in content
    assert 'id="manualCheckUpdateBtn"' in content
    assert 'id="updateCurrentCommit"' in content
    assert 'id="updateLatestCommit"' in content
    assert 'id="updateBranch"' in content
    assert 'id="updateStatusText"' in content
    assert 'id="updateCommitMsgBox"' in content

    # Verify Game Server Probe Diagnostic Banner
    assert 'id="probeDiagnosticBanner"' in content
    assert 'id="probeDiagTitle"' in content
    assert 'id="probeDiagMsg"' in content

    # Verify RBAC Gated Controls
    assert 'id="saveBtn"' in content
    assert 'id="broadcastBtn"' in content
    assert 'id="rebootHeaderBtn"' in content
    assert 'id="navPrometheusLink"' in content

    # Verify JavaScript RBAC and Cache Functions
    assert "applyRoleVisibility" in content
    assert "lastPlayersActiveCache" in content
    assert "lastPlayersOfflineCache" in content
    assert "Connected Session" in content


def test_engine_readiness_probe_diagnostics(rbac_client: TestClient):
    # 1. Unauthorized Palworld REST API probe returns 503 with explicit diagnostic message
    unauth_diag = {
        "ready": False,
        "server_name": "PalServer",
        "version": None,
        "diagnostic_code": "UNAUTHORIZED",
        "diagnostic_message": "AdminPassword mismatch in PalWorldSettings.ini. Server rejected portal credentials.",
    }
    with patch("app.main.engine.check_readiness", AsyncMock(return_value=unauth_diag)):
        res_503 = rbac_client.get("/ready")
        assert res_503.status_code == 503
        assert "AdminPassword mismatch" in res_503.json()["detail"]

    # 2. Healthy Palworld REST API probe returns 200 with OK diagnostic code
    healthy_diag = {
        "ready": True,
        "server_name": "PalServer",
        "version": "v0.3.5",
        "diagnostic_code": "OK",
        "diagnostic_message": "Palworld REST API is responding normally.",
    }
    with patch("app.main.engine.check_readiness", AsyncMock(return_value=healthy_diag)):
        res_200 = rbac_client.get("/ready")
        assert res_200.status_code == 200
        assert res_200.json()["diagnostic_code"] == "OK"
        assert res_200.json()["server_name"] == "PalServer"
