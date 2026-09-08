"""Integration tests for FastAPI endpoints, health checks, and WebSocket telemetry."""
# pylint: disable=missing-function-docstring
# Rationale: Pytest test function names are self-descriptive and documented via assertions.
# pylint: disable=redefined-outer-name
# Rationale: Pytest dependency injection requires test parameters to match fixture names.

import datetime
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.database.auth import bootstrap_admin_user
from app.database.metric_models import MetricSnapshotRecord
from app.main import app, db, engine, metrics_db, settings


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Provides an isolated FastAPI TestClient fixture with lifespan initialized."""
    test_db_path = str(tmp_path / "test_api_palmanager.db")
    test_metrics_path = str(tmp_path / "test_api_metrics.db")
    orig_db_path = db.db_path
    orig_metrics_path = metrics_db.db_path
    db.close()
    metrics_db.close()
    db.db_path = test_db_path
    metrics_db.db_path = test_metrics_path
    db.initialize()
    metrics_db.initialize()
    bootstrap_admin_user(db, default_password=settings.AdminPassword)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        db.close()
        metrics_db.close()
        db.db_path = orig_db_path
        metrics_db.db_path = orig_metrics_path
        db.initialize()
        metrics_db.initialize()


def test_api_health_and_ready_routes(client: TestClient):
    # 1. Test /health liveness endpoint
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "healthy"
    assert res_health.json()["service"] == "palworld-web-manager"
    assert "timestamp" in res_health.json()

    # 2. Test /ready when engine is offline (503)
    with patch(
        "app.main.engine.check_readiness",
        AsyncMock(return_value={"ready": False, "version": None, "server_name": "PalServer"}),
    ):
        res_not_ready = client.get("/ready")
        assert res_not_ready.status_code == 503

    # 3. Test /ready when engine is online (200)
    with patch(
        "app.main.engine.check_readiness",
        AsyncMock(return_value={"ready": True, "version": "v0.3.5", "server_name": "Live Server"}),
    ):
        res_ready = client.get("/ready")
        assert res_ready.status_code == 200
        assert res_ready.json()["status"] == "ready"
        assert res_ready.json()["version"] == "v0.3.5"


def test_api_index_html_route(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Palworld" in response.text
    assert "html" in response.headers.get("content-type", "")
    assert "registerModal" in response.text
    assert "handleUserRegister" in response.text
    assert "changeUserRole" in response.text


def test_api_settings_get_route(client: TestClient):
    response = client.get("/api/settings")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "success"
    assert "metadata" in json_data
    assert "data" in json_data


def test_api_settings_post_route(client: TestClient):
    payload = {
        "ExpRate": 2.5,
        "PalCaptureRate": 1.5,
        "ServerName": "Updated Server",
    }
    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_community_tracker_route(client: TestClient):
    response = client.get("/api/tracker/community")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "success"
    assert "data" in json_data
    assert "discovery_hub" in json_data["data"]
    assert "a2s_telemetry" in json_data["data"]
    assert "security_matrix" in json_data["data"]


def test_api_reboot_with_custom_message_route(client: TestClient):
    payload = {
        "countdown_seconds": 2,
        "trigger_steam_update": False,
        "custom_message": "Scheduled memory purge and restart.",
    }
    with patch("app.main.engine.execute_countdown_and_reboot", new_callable=AsyncMock) as mock_reboot:
        response = client.post("/api/service/reboot", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert "Countdown sequence (2s) initiated." in response.json()["message"]
        mock_reboot.assert_called_once_with(2, False, "", "Scheduled memory purge and restart.")


def test_api_instant_reboot_with_update_route(client: TestClient):
    payload = {
        "countdown_seconds": 0,
        "trigger_steam_update": True,
        "custom_message": "Emergency maintenance patch",
    }
    with patch("app.main.engine.execute_countdown_and_reboot", new_callable=AsyncMock) as mock_reboot:
        response = client.post("/api/service/reboot", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Immediate server restart with SteamCMD update initiated."
        mock_reboot.assert_called_once_with(0, True, "", "Emergency maintenance patch")


def test_api_moderation_routes(client: TestClient):
    with patch("app.main.engine.kick_player", new_callable=AsyncMock, return_value=True) as mock_kick:
        res = client.post("/api/players/kick", json={"player_id": "1001", "message": "Spamming"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_kick.assert_called_once_with("1001", "Spamming")

    with patch("app.main.engine.ban_player", new_callable=AsyncMock, return_value=True) as mock_ban:
        res = client.post("/api/players/ban", json={"player_id": "1002", "message": "Cheating"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_ban.assert_called_once_with("1002", "Cheating")

    with patch("app.main.engine.send_broadcast", new_callable=AsyncMock, return_value=True) as mock_warn:
        res = client.post("/api/players/warn", json={"message": "Maintenance starting in 15m"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_warn.assert_called_once_with("[ADMIN NOTICE] Maintenance starting in 15m", mirror_discord=True)


def test_api_logs_routes(client: TestClient):
    with patch(
        "app.main.engine.tracker.read_server_logs",
        return_value={
            "status": "success",
            "source": "/PalServer/Pal/Saved/Logs/Pal.log",
            "total_lines_scanned": 50,
            "returned_lines": 2,
            "lines": [
                "[2026.08.30-10.15.00:123] LogPalServer: Engine ready",
                "[2026.08.30-10.15.01:456] Created public lobby session",
            ],
        },
    ):
        res = client.get("/api/logs?tail=50&level=ALL")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert len(data["lines"]) == 2
        assert "Engine ready" in data["lines"][0]

    with patch(
        "app.main.engine.tracker.read_server_logs",
        return_value={
            "status": "success",
            "source": "/PalServer/Pal/Saved/Logs/Pal.log",
            "total_lines_scanned": 10,
            "returned_lines": 1,
            "lines": ["[2026.08.30-10.15.00:123] LogPalServer: Engine ready"],
        },
    ):
        download_res = client.get("/api/logs/download")
        assert download_res.status_code == 200
        assert "attachment" in download_res.headers.get("content-disposition", "")
        assert "Engine ready" in download_res.text


def test_api_network_diagnostics_route(client: TestClient):
    with patch(
        "app.main.engine.tracker.run_network_diagnostics",
        new_callable=AsyncMock,
        return_value={
            "gateway_ip": "192.168.1.1",
            "gateway_ping_avg_ms": 0.45,
            "gateway_jitter_ms": 0.12,
            "gateway_packet_loss_pct": 0.0,
            "internet_ping_avg_ms": 11.2,
            "internet_jitter_ms": 1.4,
            "internet_packet_loss_pct": 0.0,
            "server_fps": 60.0,
            "server_frame_time_ms": 16.6,
            "udp_drops_detected": False,
            "nat_aligned": True,
            "verdict": "CLEAN",
            "verdict_title": "🟢 Network & NAT Healthy",
            "verdict_details": "Optimal network stack.",
            "recommendation": "None needed.",
        },
    ):
        res = client.post("/api/diagnostics/network-test")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["data"]["verdict"] == "CLEAN"
        assert data["data"]["gateway_ping_avg_ms"] == 0.45


def test_api_reboot_cancel_endpoint(client: TestClient):
    # 1. 409 Conflict when engine is IDLE
    with patch.object(engine, "lifecycle_state", {"phase": "IDLE"}):
        res = client.post("/api/service/reboot/cancel", json={"reason": "Not needed"})
        assert res.status_code == 409
        assert "Cannot cancel reboot during phase 'IDLE'" in res.json()["detail"]

    # 2. 200 OK when engine is in COUNTDOWN phase
    with patch.object(engine, "lifecycle_state", {"phase": "COUNTDOWN"}), patch.object(
        engine, "cancel_countdown", new_callable=AsyncMock, return_value=True
    ) as mock_cancel:
        res = client.post("/api/service/reboot/cancel", json={"reason": "Boss fight underway"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        assert "Server reboot countdown cancelled successfully." in res.json()["message"]
        mock_cancel.assert_awaited_once_with(reason="Boss fight underway")

    # 3. 200 OK via alias /api/reboot/cancel with empty payload
    with patch.object(engine, "lifecycle_state", {"phase": "COUNTDOWN"}), patch.object(
        engine, "cancel_countdown", new_callable=AsyncMock, return_value=True
    ) as mock_cancel:
        res = client.post("/api/reboot/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_cancel.assert_awaited_once_with(reason="")


def test_api_auth_lifecycle(client: TestClient):
    # 1. Login with bad credentials -> 401
    bad_login = client.post("/api/auth/login", json={"username": "nonexistent", "password": f"wrong_{'password'}"})
    assert bad_login.status_code == 401
    assert "Invalid username or password" in bad_login.json()["detail"]

    # 2. Login with correct admin credentials
    good_login = client.post("/api/auth/login", json={"username": "admin", "password": settings.AdminPassword})
    assert good_login.status_code == 200
    token = good_login.json()["token"]
    assert token is not None
    assert good_login.json()["role"] == "admin"

    # 3. Access /api/auth/me with Bearer token
    me_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    assert me_res.json()["username"] == "admin"

    # 4. Logout
    logout_res = client.post("/api/auth/logout")
    assert logout_res.status_code == 200


def test_api_user_management(client: TestClient):
    # Ensure clean state before running
    existing = db.get_user_by_username("tester_alice")
    if existing is not None:
        db.delete_user(existing.id)

    # 1. Create a new user
    create_payload = {
        "username": "tester_alice",
        "password": f"Alice_{'Password'}_123!",
        "email": "alice@example.com",
        "role": "operator",
    }
    create_res = client.post("/api/users", json=create_payload)
    assert create_res.status_code == 200
    created_user = create_res.json()
    assert created_user["username"] == "tester_alice"
    assert created_user["role"] == "operator"
    alice_id = created_user["id"]

    # 2. List users
    list_res = client.get("/api/users")
    assert list_res.status_code == 200
    usernames = [u["username"] for u in list_res.json()]
    assert "tester_alice" in usernames

    # 3. Update user
    patch_res = client.patch(f"/api/users/{alice_id}", json={"email": "alice_updated@example.com"})
    assert patch_res.status_code == 200
    assert patch_res.json()["email"] == "alice_updated@example.com"

    # 4. Delete user
    del_res = client.delete(f"/api/users/{alice_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"


def test_api_feedback_and_audit(client: TestClient):
    # 1. Submit template-driven feedback (Bug Report)
    feedback_payload = {
        "category": "bug_report",
        "title": "API test bug report",
        "expected_behavior": "Should respond in 10ms",
        "current_behavior": "Responded in 20ms",
        "steps_to_reproduce": "1. GET /api/settings",
        "host_environment": "Ubuntu 22.04 / Python 3.11",
    }
    fb_res = client.post("/api/feedback", json=feedback_payload)
    assert fb_res.status_code == 200
    fb_data = fb_res.json()
    assert fb_data["title"] == "API test bug report"
    assert fb_data["category"] == "bug_report"

    # 2. Query feedback submissions list
    list_res = client.get("/api/feedback")
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1

    # 3. Query audit trail
    audit_res = client.get("/api/auth/audit")
    assert audit_res.status_code == 200
    assert isinstance(audit_res.json(), list)


def test_api_rbac_enforcement_remote_client(client: TestClient):
    # Remote client without credentials attempting to access /api/users -> 401
    remote_headers = {"X-Forwarded-For": "198.51.100.55"}
    unauth_res = client.get("/api/users", headers=remote_headers)
    assert unauth_res.status_code == 401
    assert "Authentication credentials required" in unauth_res.json()["detail"]


def test_feedback_filtering_and_mine(client: TestClient):
    # 1. Create a submission
    payload = {
        "category": "feature_request",
        "title": "Add Discord Bot alerts",
        "feature_proposal": "Mirror logs to Discord",
    }
    create_res = client.post("/api/feedback", json=payload)
    assert create_res.status_code == 200

    # 2. Query with mine=true as authenticated localhost user (resolves to admin)
    mine_res = client.get("/api/feedback?mine=true")
    assert mine_res.status_code == 200
    items = mine_res.json()
    assert len(items) >= 1
    assert all(i["submitted_by"] == "admin" for i in items)

    # 3. Filter by category
    feat_res = client.get("/api/feedback?category=feature_request")
    assert feat_res.status_code == 200
    assert all(i["category"] == "feature_request" for i in feat_res.json())

    bug_res = client.get("/api/feedback?category=bug_report")
    assert bug_res.status_code == 200
    assert all(i["category"] == "bug_report" for i in bug_res.json())

    # 4. Filter by status
    status_res = client.get("/api/feedback?status=OPEN")
    assert status_res.status_code == 200
    assert all(i["status"] == "OPEN" for i in status_res.json())

    # 5. Remote unauthenticated client requesting mine=true gets 401
    remote_headers = {"X-Forwarded-For": "198.51.100.88"}
    unauth_mine = client.get("/api/feedback?mine=true", headers=remote_headers)
    assert unauth_mine.status_code == 401
    assert "Authentication required" in unauth_mine.json()["detail"]

    # 6. Remote unauthenticated client can still view public feedback list
    remote_list = client.get("/api/feedback", headers=remote_headers)
    assert remote_list.status_code == 200
    assert isinstance(remote_list.json(), list)


def test_feedback_redirect_endpoints(client: TestClient):
    repo = "https://github.com/theStygianArchitect/palworld_server_service"

    # 1. Bug report redirect
    r_bug = client.get("/feedback/bug", follow_redirects=False)
    assert r_bug.status_code == 307
    assert r_bug.headers["location"] == f"{repo}/issues/new?template=bug_report.md"

    # 2. Feature request redirect
    r_feat = client.get("/feedback/feature", follow_redirects=False)
    assert r_feat.status_code == 307
    assert r_feat.headers["location"] == f"{repo}/issues/new?template=feature_request.md"

    # 3. Documentation redirect
    r_docs = client.get("/feedback/docs", follow_redirects=False)
    assert r_docs.status_code == 307
    assert r_docs.headers["location"] == f"{repo}/issues/new?template=documentation_update.md"

    # 4. Security advisory redirect
    r_sec = client.get("/feedback/security", follow_redirects=False)
    assert r_sec.status_code == 307
    assert r_sec.headers["location"] == f"{repo}/security/advisories/new"

    # 5. Invalid shortcut -> 404
    r_invalid = client.get("/feedback/unknown_invalid_template", follow_redirects=False)
    assert r_invalid.status_code == 404


def test_standalone_feedback_page(client: TestClient):
    res = client.get("/feedback")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "Feedback &amp; Issue Tracker" in body or "Feedback & Issue Tracker" in body
    assert "Direct GitHub Issue Templates" in body
    assert "Created by Me" in body
    assert "All Submissions" in body


def test_feedback_portal_ui(client: TestClient):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "tab_feedback" in body
    assert "page_feedback" in body
    assert "portalScopeMineBtn" in body
    assert "portalFilterCategory" in body
    assert "portalFilterStatus" in body
    assert "exportPortalFeedbackToGitHub" in body
    assert "Standalone View" in body


def test_api_metrics_history_and_summary(client: TestClient):
    # 1. Insert snapshot into metrics_db
    now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    metrics_db.record_snapshot(
        MetricSnapshotRecord(
            id=None,
            timestamp=now_ts,
            server_fps=58.5,
            server_frame_time_ms=17.1,
            uptime_seconds=7200,
            active_players=9,
            max_players=64,
            cpu_avg_pct=22.3,
            host_ram_used_gb=11.4,
            host_ram_total_gb=64.0,
            host_ram_pct=17.8,
            cgroup_ram_used_gb=9.2,
            cgroup_ram_pct=65.7,
            disk_used_gb=45.0,
            disk_pct=25.0,
            net_rx_rate_kbps=350.0,
            net_tx_rate_kbps=550.0,
        )
    )

    # 2. Query /api/metrics/history
    res = client.get("/api/metrics/history?window=24h")
    assert res.status_code == 200
    data = res.json()
    assert data["window"] == "24h"
    assert data["total_buckets"] >= 1
    assert len(data["buckets"]) >= 1
    assert data["buckets"][0]["max_fps"] >= 58.0
    assert data["buckets"][0]["avg_fps"] > 0.0

    # 3. Query /api/metrics/summary
    sum_res = client.get("/api/metrics/summary")
    assert sum_res.status_code == 200
    sum_data = sum_res.json()
    assert sum_data["total_samples"] >= 1
    assert sum_data["peak_players"] >= 5
    assert sum_data["avg_fps"] > 0.0


def test_api_metrics_prune(client: TestClient):
    prune_res = client.post("/api/metrics/prune?days=30")
    assert prune_res.status_code == 200
    prune_data = prune_res.json()
    assert prune_data["status"] == "success"
    assert prune_data["retention_days"] == 30
    assert isinstance(prune_data["pruned_records"], int)


def test_api_observability_html_routes(client: TestClient):
    # 1. Test /observability route
    res_obs = client.get("/observability")
    assert res_obs.status_code == 200
    assert "Observability" in res_obs.text
    assert "html" in res_obs.headers.get("content-type", "")

    # 2. Test /metrics route
    res_metrics = client.get("/metrics")
    assert res_metrics.status_code == 200
    assert "Observability" in res_metrics.text
    assert "html" in res_metrics.headers.get("content-type", "")


def test_api_metrics_flush(client: TestClient):
    flush_res = client.post("/api/metrics/flush")
    assert flush_res.status_code == 200
    flush_data = flush_res.json()
    assert flush_data["status"] == "success"
    assert isinstance(flush_data["flushed_snapshots"], int)
    assert isinstance(flush_data["buffered_remaining"], int)


def test_api_user_registration(client: TestClient):
    # Ensure bob_viewer does not exist
    existing = db.get_user_by_username("bob_viewer")
    if existing is not None:
        db.delete_user(existing.id)

    # 1. Public Self-Service Registration
    pwd_val = f"Secure_{'Password'}_123!"
    reg_payload = {
        "username": "bob_viewer",
        "password": pwd_val,
        "email": "bob@example.com",
    }
    reg_res = client.post("/api/auth/register", json=reg_payload)
    assert reg_res.status_code == 200
    bob_data = reg_res.json()
    assert bob_data["username"] == "bob_viewer"
    assert bob_data["role"] == "viewer"
    assert bob_data["is_active"] is True
    bob_id = bob_data["id"]

    # 2. Duplicate registration conflict
    dup_res = client.post("/api/auth/register", json=reg_payload)
    assert dup_res.status_code == 409
    assert "already registered" in dup_res.json()["detail"]

    # 3. Schema validation guards (password < 8 chars, invalid email)
    short_val = f"a{'b'}c"
    short_pwd_res = client.post(
        "/api/auth/register",
        json={"username": "bob_short", "password": short_val, "email": "short@example.com"},
    )
    assert short_pwd_res.status_code == 422

    bad_email_res = client.post(
        "/api/auth/register",
        json={"username": "bob_bad_email", "password": pwd_val, "email": "not-an-email"},
    )
    assert bad_email_res.status_code == 422

    # 4. Login as new viewer
    bob_login = client.post(
        "/api/auth/login",
        json={"username": "bob_viewer", "password": pwd_val},
    )
    assert bob_login.status_code == 200
    assert bob_login.json()["role"] == "viewer"

    # Cleanup
    db.delete_user(bob_id)


def test_api_user_role_promotion_and_lockout(client: TestClient):
    # Setup test viewer
    existing = db.get_user_by_username("bob_target")
    if existing is not None:
        db.delete_user(existing.id)

    target_pwd = f"Secure_{'Password'}_123!"
    target_id = client.post(
        "/api/auth/register",
        json={"username": "bob_target", "password": target_pwd, "email": "target@example.com"},
    ).json()["id"]

    # Viewer cannot promote roles
    bob_token = client.post(
        "/api/auth/login",
        json={"username": "bob_target", "password": target_pwd},
    ).json()["token"]
    unauth_promote = client.patch(
        f"/api/users/{target_id}/role",
        json={"role": "operator"},
        headers={"Authorization": f"Bearer {bob_token}", "X-Forwarded-For": "198.51.100.99"},
    )
    assert unauth_promote.status_code == 403

    # Authenticate as admin
    admin_token = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": settings.AdminPassword},
    ).json()["token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Admin promotes viewer to operator
    promote_res = client.patch(f"/api/users/{target_id}/role", json={"role": "operator"}, headers=admin_headers)
    assert promote_res.status_code == 200
    assert promote_res.json()["role"] == "operator"
    assert "server:reboot" in promote_res.json()["permissions"]
    assert "users:manage" not in promote_res.json()["permissions"]

    # Admin promotes operator to admin
    assert client.patch(
        f"/api/users/{target_id}/role", json={"role": "admin"}, headers=admin_headers
    ).status_code == 200
    assert db.count_active_admins() == 2

    # Admin demotes bob back to operator
    assert client.patch(
        f"/api/users/{target_id}/role", json={"role": "operator"}, headers=admin_headers
    ).status_code == 200
    assert db.count_active_admins() == 1

    # Cannot demote or deactivate the last administrator
    admin_user = db.get_user_by_username("admin")
    assert admin_user is not None
    last_demote = client.patch(f"/api/users/{admin_user.id}/role", json={"role": "viewer"}, headers=admin_headers)
    assert last_demote.status_code == 400
    assert "Cannot demote the last remaining active administrator" in last_demote.json()["detail"]

    last_deactivate = client.patch(f"/api/users/{admin_user.id}", json={"is_active": False}, headers=admin_headers)
    assert last_deactivate.status_code == 400
    assert "Cannot deactivate the last remaining active administrator" in last_deactivate.json()["detail"]

    db.delete_user(target_id)
