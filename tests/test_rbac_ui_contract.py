"""Contract and integration tests for RBAC UI gating, diagnostics, and update check."""
# pylint: disable=missing-function-docstring
# Rationale: Pytest test function names are self-descriptive and documented via assertions.
# pylint: disable=redefined-outer-name
# Rationale: Pytest dependency injection requires test parameters to match fixture names.

import re
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


def _create_user_and_token(client: TestClient, username: str, password: str, role: str) -> str:
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
    assert rbac_client.post("/api/players/kick", json={"player_id": "p1"}, headers=viewer_headers).status_code == 403
    assert rbac_client.post("/api/players/ban", json={"player_id": "p1"}, headers=viewer_headers).status_code == 403

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
    """Verify dashboard UI RBAC gating elements and probe diagnostic banners.

    Regression test for Issue #16: RBAC permission matrix enforcement and PalServer REST probe diagnostics.
    """
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
    """Verify PalServer engine readiness probe returns diagnostic details on failure and success.

    Regression test for Issue #16: RBAC permission matrix enforcement and PalServer REST probe diagnostics.
    """
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


# pylint: disable=too-many-branches,too-many-statements,too-many-nested-blocks,too-many-locals
# Rationale: Lexical analysis of inline JavaScript requires multi-state parsing and token dispatching.
def test_index_html_javascript_syntax_integrity():
    """Verify that all inline JavaScript in index.html is syntactically balanced and parseable.

    Regression test for Issue #19: Frontend JavaScript syntax tokenizer and template literal integrity.
    """
    html_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "index.html"
    content = html_path.read_text(encoding="utf-8")

    scripts = re.findall(r"<script(?:\s+[^>]*)?>(.*?)</script>", content, re.DOTALL)
    assert len(scripts) >= 2, "Expected at least 2 inline script tags in index.html"

    for script_idx, js_code in enumerate(scripts):
        state_stack = ["NORMAL"]
        brace_stack: list[tuple[str, int, int]] = []
        errors: list[str] = []

        lines = js_code.splitlines()
        for line_idx, line in enumerate(lines):
            col = 0
            while col < len(line):
                ch = line[col]
                curr_state = state_stack[-1]

                if curr_state == "COMMENT_LINE":
                    break
                if curr_state == "COMMENT_BLOCK":
                    if ch == "*" and col + 1 < len(line) and line[col + 1] == "/":
                        state_stack.pop()
                        col += 2
                        continue
                    col += 1
                    continue
                if curr_state == "STRING_SINGLE":
                    if ch == "\\":
                        col += 2
                        continue
                    if ch == "'":
                        state_stack.pop()
                    col += 1
                    continue
                if curr_state == "STRING_DOUBLE":
                    if ch == "\\":
                        col += 2
                        continue
                    if ch == '"':
                        state_stack.pop()
                    col += 1
                    continue
                if curr_state == "STRING_TEMPLATE":
                    if ch == "\\":
                        col += 2
                        continue
                    if ch == "`":
                        state_stack.pop()
                        col += 1
                        continue
                    if ch == "$" and col + 1 < len(line) and line[col + 1] == "{":
                        state_stack.append("TEMPLATE_EXPR")
                        brace_stack.append(("EXPR", line_idx + 1, col + 1))
                        col += 2
                        continue
                    col += 1
                    continue

                # NORMAL or TEMPLATE_EXPR
                if ch == "/" and col + 1 < len(line) and line[col + 1] == "/":
                    state_stack.append("COMMENT_LINE")
                    break
                if ch == "/" and col + 1 < len(line) and line[col + 1] == "*":
                    state_stack.append("COMMENT_BLOCK")
                    col += 2
                    continue
                if ch == "'":
                    state_stack.append("STRING_SINGLE")
                    col += 1
                    continue
                if ch == '"':
                    state_stack.append("STRING_DOUBLE")
                    col += 1
                    continue
                if ch == "`":
                    state_stack.append("STRING_TEMPLATE")
                    col += 1
                    continue
                if ch == "{":
                    brace_stack.append(("{", line_idx + 1, col + 1))
                    col += 1
                    continue
                if ch == "}":
                    if not brace_stack:
                        errors.append(f"Unexpected '}}' at script #{script_idx} line {line_idx + 1}:{col + 1}")
                    else:
                        top_type, _, _ = brace_stack.pop()
                        if top_type == "EXPR":
                            if state_stack[-1] == "TEMPLATE_EXPR":
                                state_stack.pop()
                            else:
                                errors.append(f"Template expression mismatch at line {line_idx + 1}:{col + 1}")
                    col += 1
                    continue
                col += 1

            if state_stack and state_stack[-1] == "COMMENT_LINE":
                state_stack.pop()

        assert not errors, f"Syntax errors detected in script #{script_idx}: {errors}"
        assert not brace_stack, f"Unclosed braces in script #{script_idx}: {brace_stack}"
        assert state_stack == ["NORMAL"], f"Unterminated state in script #{script_idx}: {state_stack}"


def test_dashboard_ui_tls_elements_present(rbac_client: TestClient) -> None:
    """Verifies that HTTPS header badge, TLS security card, and TLS modal are present in dashboard HTML."""
    resp = rbac_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="httpsHeaderBadge"' in html
    assert 'id="tlsCard"' in html
    assert 'id="tlsModal"' in html
    assert 'id="btnRenewTlsNow"' in html
    assert "fetchTlsStatus()" in html
    assert "triggerTlsRenewal()" in html
    assert "setVisible('updatesTlsCard', isAdmin)" in html
    assert "setVisible('settingsTlsCard', isAdmin)" in html
    assert "setVisible('tlsCard', isAdmin)" in html
    assert "setVisible('httpsHeaderBadge', isAdmin)" in html


def test_dashboard_ui_deployment_progression_elements_present(rbac_client: TestClient) -> None:
    """Verifies that unified deployment progression, steppers, and TLS cards are present in HTML."""
    resp = rbac_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="postUpdateSuccessBanner"' in html
    assert 'id="postUpdateFailedBanner"' in html
    assert 'id="updatesTlsCard"' in html
    assert 'id="settingsTlsCard"' in html
    assert 'id="deployStepperContainer"' in html
    assert 'id="deployProgressBar"' in html
    assert 'id="tlsStepperContainer"' in html
    assert 'id="tlsProgressBar"' in html
    assert 'id="rebootProgressBar"' in html
    assert "acknowledgePostUpdate()" in html
    assert "pollDeployProgress()" in html


def test_dashboard_ui_updater_dto_contracts(rbac_client: TestClient) -> None:
    """Verifies that index.html aligns with backend DTO models for updates and validation error handling."""
    resp = rbac_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    # DTO alignment
    assert "data.latest_commit" in html
    assert "data.last_update.deployed_commit" in html
    assert "latestUpdateStatus.target_branch" in html
    # Validation error formatting (avoid [object Object])
    assert "Array.isArray(data.detail)" in html
    assert "Array.isArray(msg)" in html
    assert "whitespace-pre-line" in html


def test_dashboard_header_canonical_link_contract() -> None:
    """Regression test for Issue #40: Header branding title navigation anchor and canonical HTTPS contract."""
    html_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "index.html"
    assert html_path.exists(), f"Expected template file at {html_path}"
    html = html_path.read_text(encoding="utf-8")

    assert 'id="headerSuiteLogoLink"' in html
    assert 'href="https://thestygianarchitect.duckdns.org:8080"' in html
    assert 'title="Navigate to Canonical Secure Portal (HTTPS)"' in html

    anchor_match = re.search(r'<a[^>]*id="headerSuiteLogoLink"[^>]*>(.*?)</a>', html, re.DOTALL)
    assert anchor_match is not None, "Anchor element with id='headerSuiteLogoLink' not found in index.html"
    assert "Palworld Server Operations Suite" in anchor_match.group(1)

    scripts = re.findall(r"<script(?:\s+[^>]*)?>(.*?)</script>", html, re.DOTALL)
    script_section = "\n".join(scripts)

    assert "headerSuiteLogoLink" in script_section
    assert "data.canonical_url" in script_section
    assert (
        "logoLink.href = data.canonical_url" in script_section
        or "headerSuiteLogoLink.href = data.canonical_url" in script_section
    )
    assert "window.location.protocol === 'http:'" in script_section
