"""Integration and unit tests for real-time deployment progression and post-update reporting."""
# pylint: disable=redefined-outer-name,missing-function-docstring
# Rationale: Standard pytest idioms with fixtures and self-describing test functions.

import json
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database.auth import bootstrap_admin_user
from app.main import app, db, metrics_db, settings, updater


@pytest.fixture
def client_fixture(tmp_path: Path) -> Generator[tuple[TestClient, str, str], None, None]:
    """Provides isolated TestClient with admin and viewer tokens."""
    test_db_path = str(tmp_path / "test_prog_palmanager.db")
    test_metrics_path = str(tmp_path / "test_prog_metrics.db")
    orig_db_path = db.db_path
    orig_metrics_path = metrics_db.db_path
    orig_updater_enabled = settings.updater_enabled
    settings.updater_enabled = False
    db.close()
    metrics_db.close()
    db.db_path = test_db_path
    metrics_db.db_path = test_metrics_path
    db.initialize()
    metrics_db.initialize()
    bootstrap_admin_user(db, default_password=settings.AdminPassword)

    try:
        with TestClient(app) as test_client:
            # Login as admin
            admin_login = test_client.post(
                "/api/auth/login", json={"username": "admin", "password": settings.AdminPassword}
            )
            admin_token = admin_login.json()["token"]
            test_pwd = f"Viewer_{'Pass'}_123!"

            # Create and login viewer
            create_viewer_res = test_client.post(
                "/api/users",
                json={
                    "username": "test_viewer_prog",
                    "password": test_pwd,
                    "role": "viewer",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert create_viewer_res.status_code == 200

            viewer_login = test_client.post(
                "/api/auth/login",
                json={"username": "test_viewer_prog", "password": test_pwd},
            )
            assert viewer_login.status_code == 200
            viewer_token = viewer_login.json()["token"]

            yield test_client, admin_token, viewer_token
    finally:
        settings.updater_enabled = orig_updater_enabled
        db.close()
        metrics_db.close()
        db.db_path = orig_db_path
        metrics_db.db_path = orig_metrics_path
        db.initialize()
        metrics_db.initialize()


def test_get_deploy_progress_idle(client_fixture: tuple[TestClient, str, str], tmp_path: Path):
    client, admin_token, _ = client_fixture

    lock_file = tmp_path / "test_prog_idle.lock"
    post_update_file = tmp_path / "last_update_idle.json"
    updater.lock_file = lock_file
    updater.post_update_file = post_update_file

    resp = client.get("/api/system/deploy/progress", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is False
    assert data["percentage"] == 0
    assert len(data["steps"]) == 5
    assert data["last_update"] is None


def test_get_deploy_progress_active_streaming(client_fixture: tuple[TestClient, str, str], tmp_path: Path):
    client, admin_token, _ = client_fixture

    lock_file = tmp_path / "test_prog_active.lock"
    post_update_file = tmp_path / "last_update_active.json"
    log_file = tmp_path / "deploy.log"

    updater.lock_file = lock_file
    updater.post_update_file = post_update_file
    updater.deploy_log_path = log_file

    now_epoch = time.time()
    lock_file.write_text(
        f"pid=9999\nstarted_at={now_epoch - 20.0}\ntimestamp={now_epoch}\nbranch=main\noperation=portal_update\n",
        encoding="utf-8",
    )
    log_file.write_text(
        "[STEP 1/5] Pulling latest updates from origin/main... [ OK ]\n"
        "[STEP 2/5] Syncing application code & systemd units... [ OK ]\n"
        "[STEP 3/5] Enforcing cross-user POSIX ACLs and storage permissions...\n",
        encoding="utf-8",
    )

    # Directly check updater calculation
    progress = updater.get_deployment_progress(log_path=log_file)
    assert progress.active is True
    assert progress.current_step == 3
    assert progress.steps[0].status == "completed"
    assert progress.steps[1].status == "completed"
    assert progress.steps[2].status == "running"
    assert progress.percentage == 50
    assert progress.elapsed_seconds >= 19

    # Call REST endpoint
    resp = client.get("/api/system/deploy/progress", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["active"] is True
    assert res_data["current_step"] == 3


def test_get_deploy_progress_completed_unacknowledged(client_fixture: tuple[TestClient, str, str], tmp_path: Path):
    client, admin_token, _ = client_fixture

    lock_file = tmp_path / "test_prog_completed.lock"
    post_update_file = tmp_path / "last_update_comp.json"
    updater.lock_file = lock_file
    updater.post_update_file = post_update_file

    post_update_file.write_text(
        json.dumps({
            "status": "success",
            "target_branch": "main",
            "deployed_commit": "abcdef1234567890abcdef1234567890abcdef12",
            "deployed_commit_short": "abcdef1",
            "deployed_at": "2026-09-09T12:00:00Z",
            "duration_seconds": 38,
            "summary": "feat: unified deployment progression",
            "acknowledged": False,
        }),
        encoding="utf-8",
    )

    resp = client.get("/api/system/deploy/progress", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is False
    assert data["percentage"] == 100
    assert data["current_step"] == 5
    assert data["last_update"] is not None
    assert data["last_update"]["deployed_commit_short"] == "abcdef1"
    assert data["last_update"]["acknowledged"] is False


def test_post_update_acknowledge_rbac(client_fixture: tuple[TestClient, str, str], tmp_path: Path):
    client, admin_token, viewer_token = client_fixture

    post_update_file = tmp_path / "last_update_ack.json"
    updater.post_update_file = post_update_file
    post_update_file.write_text(
        json.dumps({
            "status": "success",
            "target_branch": "main",
            "deployed_commit": "abcdef1234567890abcdef1234567890abcdef12",
            "deployed_commit_short": "abcdef1",
            "deployed_at": "2026-09-09T12:00:00Z",
            "duration_seconds": 38,
            "summary": "feat: update",
            "acknowledged": False,
        }),
        encoding="utf-8",
    )

    # Viewer should be forbidden (403)
    resp_viewer = client.post(
        "/api/system/update/acknowledge", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert resp_viewer.status_code == 403

    # Admin should succeed (200)
    resp_admin = client.post(
        "/api/system/update/acknowledge", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp_admin.status_code == 200
    assert resp_admin.json()["status"] == "success"

    # Verify acknowledged flag persisted
    summary = updater.get_last_update_summary()
    assert summary is not None
    assert summary.acknowledged is True


def test_update_status_includes_last_update(client_fixture: tuple[TestClient, str, str], tmp_path: Path):
    client, admin_token, _ = client_fixture
    post_update_file = tmp_path / "last_update_status.json"
    updater.post_update_file = post_update_file
    post_update_file.write_text(
        json.dumps({
            "status": "success",
            "target_branch": "main",
            "deployed_commit": "9876543210fedcba",
            "deployed_commit_short": "9876543",
            "deployed_at": "2026-09-09T12:00:00Z",
            "duration_seconds": 25,
            "summary": "feat: fast update",
            "acknowledged": False,
        }),
        encoding="utf-8",
    )

    resp = client.get("/api/system/update/status", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "last_update" in data
    assert data["last_update"]["deployed_commit_short"] == "9876543"
