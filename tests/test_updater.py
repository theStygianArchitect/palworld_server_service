"""Unit tests for UpdateWatcher background telemetry, lock management, and deployment execution."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# Rationale: Standard pytest idioms with fixtures and self-describing test functions.

import asyncio
import os
import shutil
import subprocess  # nosec B404 - required for mocking Popen in test harness
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.engine.updater import (
    DEFAULT_DEPLOY_RUNNER,
    STALE_LOCK_TIMEOUT_SECONDS,
    UpdateWatcher,
    _resolve_default_deploy_log_path,
    _spawn_detached_deployer,
)


@pytest.fixture
def temp_watcher(tmp_path: Path) -> UpdateWatcher:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    lock_file = tmp_path / "palmanager_update.lock"
    deploy_script = tmp_path / "deploy.sh"
    deploy_script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")

    return UpdateWatcher(
        repo_url="https://github.com/theStygianArchitect/palworld_server_service",
        branch="main",
        check_interval_seconds=10,
        deploy_script=deploy_script,
        lock_file=lock_file,
        repo_dir=repo_dir,
    )


def test_parse_repo_slug():
    owner, repo = UpdateWatcher.parse_repo_slug("https://github.com/theStygianArchitect/palworld_server_service")
    assert owner == "theStygianArchitect"
    assert repo == "palworld_server_service"

    # Trailing .git
    owner, repo = UpdateWatcher.parse_repo_slug("https://github.com/theStygianArchitect/palworld_server_service.git")
    assert owner == "theStygianArchitect"
    assert repo == "palworld_server_service"

    # Invalid URL
    with pytest.raises(ValueError, match="Invalid GitHub repository URL format"):
        UpdateWatcher.parse_repo_slug("https://github.com/")


def test_get_local_commit_metadata_fallback(temp_watcher: UpdateWatcher):
    meta_file = temp_watcher.repo_dir / ".git_commit"
    meta_file.write_text("a1b2c3d4e5f6\n", encoding="utf-8")
    assert temp_watcher.get_local_commit() == "a1b2c3d4e5f6"


def test_get_local_commit_unknown(temp_watcher: UpdateWatcher):
    assert temp_watcher.get_local_commit() == "unknown"


def test_is_update_in_progress_no_lock(temp_watcher: UpdateWatcher):
    assert temp_watcher.is_update_in_progress() is False


def test_is_update_in_progress_active_lock(temp_watcher: UpdateWatcher):
    temp_watcher.lock_file.write_text("pid=123\n", encoding="utf-8")
    assert temp_watcher.is_update_in_progress() is True


def test_is_update_in_progress_stale_lock(temp_watcher: UpdateWatcher):
    temp_watcher.lock_file.write_text("pid=123\n", encoding="utf-8")
    # Set mtime back beyond threshold
    past_time = time.time() - (STALE_LOCK_TIMEOUT_SECONDS + 100.0)
    os.utime(temp_watcher.lock_file, (past_time, past_time))

    assert temp_watcher.is_update_in_progress() is False
    assert not temp_watcher.lock_file.exists()


def test_resolve_deploy_script(temp_watcher: UpdateWatcher):
    script = temp_watcher.resolve_deploy_script()
    assert script == temp_watcher.deploy_script


@pytest.mark.asyncio
async def test_check_for_updates_matching(temp_watcher: UpdateWatcher):
    fake_sha = "e05924d123456789"
    (temp_watcher.repo_dir / ".git_commit").write_text(fake_sha, encoding="utf-8")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "sha": fake_sha,
        "commit": {"message": "feat(core): match commit\nDetails here"},
    }

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        status = await temp_watcher.check_for_updates()
        assert status.update_available is False
        assert status.commits_behind == 0
        assert status.current_commit == fake_sha
        assert status.latest_commit == fake_sha
        assert status.latest_commit_message == "feat(core): match commit"
        assert status.error is None


@pytest.mark.asyncio
async def test_check_for_updates_diverged(temp_watcher: UpdateWatcher):
    local_sha = "e05924d123456789"
    remote_sha = "89fdccc987654321"
    (temp_watcher.repo_dir / ".git_commit").write_text(local_sha, encoding="utf-8")

    def mock_get(url, headers):
        resp = MagicMock()
        resp.status_code = 200
        if "compare" in url:
            resp.json.return_value = {"ahead_by": 3}
        else:
            resp.json.return_value = {
                "sha": remote_sha,
                "commit": {"message": "feat(engine): new remote features"},
            }
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        status = await temp_watcher.check_for_updates()
        assert status.update_available is True
        assert status.commits_behind == 3
        assert status.current_commit == local_sha
        assert status.latest_commit == remote_sha
        assert status.latest_commit_message == "feat(engine): new remote features"
        assert status.error is None


@pytest.mark.asyncio
async def test_check_for_updates_rate_limit(temp_watcher: UpdateWatcher):
    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        status = await temp_watcher.check_for_updates()
        assert "rate limit" in (status.error or "").lower()


@pytest.mark.asyncio
async def test_check_for_updates_network_error(temp_watcher: UpdateWatcher):
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("Network offline")):
        status = await temp_watcher.check_for_updates()
        assert status.error == "Network error connecting to GitHub API"


@pytest.mark.asyncio
async def test_apply_update_success_with_runner(temp_watcher: UpdateWatcher):
    runner_calls = []

    def mock_runner(script: str, branch: str):
        runner_calls.append((script, branch))

    temp_watcher.command_runner = mock_runner
    resp = await temp_watcher.apply_update(branch="main")

    assert resp.status == "applying"
    assert resp.target_branch == "main"
    assert len(runner_calls) == 1
    assert runner_calls[0][1] == "main"
    assert temp_watcher.lock_file.exists()


@pytest.mark.asyncio
async def test_apply_update_invalid_branch(temp_watcher: UpdateWatcher):
    with pytest.raises(HTTPException) as exc_info:
        await temp_watcher.apply_update(branch="invalid; rm -rf")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_apply_update_conflict(temp_watcher: UpdateWatcher):
    temp_watcher.lock_file.write_text("pid=999\n", encoding="utf-8")
    with pytest.raises(HTTPException) as exc_info:
        await temp_watcher.apply_update(branch="main")
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_apply_update_missing_script(temp_watcher: UpdateWatcher):
    temp_watcher.deploy_script = Path("/nonexistent/deploy.sh")
    with pytest.raises(HTTPException) as exc_info:
        await temp_watcher.apply_update(branch="main")
    assert exc_info.value.status_code == 500
    assert not temp_watcher.lock_file.exists()


@pytest.mark.asyncio
async def test_run_loop_cancellation(temp_watcher: UpdateWatcher):
    with patch.object(temp_watcher, "check_for_updates", AsyncMock(return_value=temp_watcher.get_status())):
        task = asyncio.create_task(temp_watcher.run_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        await task


def test_resolve_default_deploy_log_path():
    log_path = _resolve_default_deploy_log_path()
    assert isinstance(log_path, Path)
    assert log_path.name == "deploy.log"


def test_get_last_update_summary_missing(temp_watcher: UpdateWatcher):
    assert temp_watcher.get_last_update_summary() is None


def test_get_last_update_summary_present(temp_watcher: UpdateWatcher, tmp_path: Path):
    summary_file = tmp_path / "last_update.json"
    summary_file.write_text(
        '{"status": "success", "target_branch": "main", "deployed_commit": "1234567890abcdef", '
        '"deployed_commit_short": "1234567", "deployed_at": "2026-09-09T12:00:00Z", '
        '"duration_seconds": 42, "summary": "Fix all bugs", "acknowledged": false}',
        encoding="utf-8",
    )
    temp_watcher.post_update_file = summary_file
    summary = temp_watcher.get_last_update_summary()
    assert summary is not None
    assert summary.status == "success"
    assert summary.deployed_commit_short == "1234567"
    assert summary.duration_seconds == 42
    assert summary.acknowledged is False

    # Test acknowledgement
    assert temp_watcher.acknowledge_last_update() is True
    updated = temp_watcher.get_last_update_summary()
    assert updated is not None
    assert updated.acknowledged is True


def test_get_deployment_progress_idle(temp_watcher: UpdateWatcher):
    prog = temp_watcher.get_deployment_progress()
    assert prog.active is False
    assert prog.current_step == 0
    assert prog.percentage == 0
    assert len(prog.steps) == 5


def test_get_deployment_progress_active_and_parsing(temp_watcher: UpdateWatcher, tmp_path: Path):
    # Setup active lock
    temp_watcher.lock_file.write_text(
        f"pid=1234\nstarted_at={time.time() - 15.0}\nbranch=main\noperation=portal_update\n",
        encoding="utf-8",
    )
    # Setup mock deploy.log
    log_file = tmp_path / "deploy.log"
    log_file.write_text(
        "[STEP 1/5] Pulling latest updates from origin/main... [ OK ]\n"
        "[STEP 2/5] Syncing application code & systemd units... [ OK ]\n"
        "[STEP 3/5] Enforcing cross-user POSIX ACLs and storage permissions...\n",
        encoding="utf-8",
    )

    prog = temp_watcher.get_deployment_progress(log_path=log_file)
    assert prog.active is True
    assert prog.current_step == 3
    assert prog.steps[0].status == "completed"
    assert prog.steps[1].status == "completed"
    assert prog.steps[2].status == "running"
    assert prog.steps[3].status == "pending"
    assert prog.steps[4].status == "pending"
    assert prog.percentage == 50  # 2 completed (40) + running (10) = 50
    assert prog.elapsed_seconds >= 14
    assert prog.estimated_remaining_seconds is not None
    assert len(prog.log_tail) == 3


def test_get_status_includes_target_branch(temp_watcher: UpdateWatcher) -> None:
    """Verifies that UpdateStatusResponse includes target_branch matching watcher branch."""
    temp_watcher.branch = "feature-xyz"
    status = temp_watcher.get_status()
    assert status.target_branch == "feature-xyz"


def test_spawn_detached_deployer_uses_non_interactive_sudo(tmp_path: Path, monkeypatch) -> None:
    """Verifies that on posix systems, _spawn_detached_deployer invokes sudo with -n."""
    captured_cmds = []

    def mock_popen(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        return MagicMock(pid=99999)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr("app.engine.updater._resolve_default_deploy_log_path", lambda: tmp_path / "deploy.log")
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/sudo" if x == "sudo" else None)
    monkeypatch.setattr(shutil, "copy2", lambda src, dst: dst)
    monkeypatch.setattr(os, "chmod", lambda path, mode: None)

    script_path = tmp_path / "deploy.sh"
    script_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")

    _spawn_detached_deployer(script_path, "main")
    assert len(captured_cmds) == 1
    assert captured_cmds[0] == ["/usr/bin/sudo", "-n", str(DEFAULT_DEPLOY_RUNNER), "main"]


def test_spawn_detached_deployer_stages_out_of_tree_runner(monkeypatch, tmp_path: Path) -> None:
    """Verifies staging of deploy script to isolated runner and fallback on OSError."""
    captured_cmds: list[list[str]] = []
    copied_files: list[tuple[str, str]] = []
    chmod_calls: list[tuple[str, int]] = []

    def mock_popen(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        return MagicMock(pid=12345)

    def mock_copy2(src, dst):
        copied_files.append((str(src), str(dst)))
        return dst

    def mock_chmod(path, mode):
        chmod_calls.append((str(path), mode))

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr("app.engine.updater._resolve_default_deploy_log_path", lambda: tmp_path / "deploy.log")
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/sudo" if x == "sudo" else None)
    monkeypatch.setattr(shutil, "copy2", mock_copy2)
    monkeypatch.setattr(os, "chmod", mock_chmod)

    mock_deploy = tmp_path / "deploy.sh"
    mock_deploy.write_text("#!/bin/bash\necho deploy\n", encoding="utf-8")

    # 1. Success case: deploy.sh is staged out-of-tree and executed
    _spawn_detached_deployer(mock_deploy, "main")

    assert len(copied_files) == 1
    assert copied_files[0] == (str(mock_deploy), str(DEFAULT_DEPLOY_RUNNER))
    assert len(chmod_calls) == 1
    assert chmod_calls[0] == (str(DEFAULT_DEPLOY_RUNNER), 0o755)
    assert len(captured_cmds) == 1
    assert captured_cmds[0] == ["/usr/bin/sudo", "-n", str(DEFAULT_DEPLOY_RUNNER), "main"]

    # 2. Defensive fallback case: shutil.copy2 raises OSError
    copied_files.clear()
    chmod_calls.clear()
    captured_cmds.clear()

    def mock_copy2_error(src, dst):
        raise OSError("Read-only filesystem or disk full")

    monkeypatch.setattr(shutil, "copy2", mock_copy2_error)

    _spawn_detached_deployer(mock_deploy, "feature-branch")

    assert len(captured_cmds) == 1
    assert captured_cmds[0] == ["/usr/bin/sudo", "-n", str(mock_deploy), "feature-branch"]


def test_get_deployment_progress_aborted_in_log(temp_watcher: UpdateWatcher, tmp_path: Path):
    """Verifies that when deploy.log contains an abort message, the active step is marked failed and active=False."""
    temp_watcher.lock_file.write_text(
        f"pid=1234\nstarted_at={time.time() - 10.0}\nbranch=main\noperation=portal_update\n",
        encoding="utf-8",
    )
    log_file = tmp_path / "deploy.log"
    log_file.write_text(
        "[STEP 1/5] Pulling latest updates from origin/main...\n"
        "git config --global --add safe.directory /home/tsa/palworld_server_service\n"
        "[-] Deployment aborted with error exit code: 128\n",
        encoding="utf-8",
    )

    prog = temp_watcher.get_deployment_progress(log_path=log_file)
    assert prog.active is False
    assert prog.current_step == 1
    assert prog.steps[0].status == "failed"
    assert "Deployment aborted at step 1" in prog.step_name


def test_get_deployment_progress_failed_post_update_record(temp_watcher: UpdateWatcher, tmp_path: Path):
    """Verifies that an inactive deployment with a failed last_update record reports failure."""
    post_update_file = tmp_path / "last_update.json"
    temp_watcher.post_update_file = post_update_file
    post_update_file.write_text(
        '{"status": "failed", "error_message": "Deployment aborted with exit code 128", "exit_code": 128}',
        encoding="utf-8",
    )

    prog = temp_watcher.get_deployment_progress()
    assert prog.active is False
    assert prog.percentage == 0
    assert prog.steps[0].status == "failed"
    assert prog.last_update is not None
    assert prog.last_update.status == "failed"
