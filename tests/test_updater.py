"""Unit tests for UpdateWatcher background telemetry, lock management, and deployment execution."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# Rationale: Standard pytest idioms with fixtures and self-describing test functions.

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.engine.updater import (
    STALE_LOCK_TIMEOUT_SECONDS,
    UpdateWatcher,
    _resolve_default_deploy_log_path,
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
    owner, repo = UpdateWatcher.parse_repo_slug(
        "https://github.com/theStygianArchitect/palworld_server_service"
    )
    assert owner == "theStygianArchitect"
    assert repo == "palworld_server_service"

    # Trailing .git
    owner, repo = UpdateWatcher.parse_repo_slug(
        "https://github.com/theStygianArchitect/palworld_server_service.git"
    )
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
    task = asyncio.create_task(temp_watcher.run_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    await task


def test_resolve_default_deploy_log_path():
    log_path = _resolve_default_deploy_log_path()
    assert isinstance(log_path, Path)
    assert log_path.name == "deploy.log"
