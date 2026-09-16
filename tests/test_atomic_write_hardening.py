"""Tests verifying that disk writes across core engines adhere to atomic persistence contracts."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.database.auth import _export_credentials_to_file
from app.engine.acme_client import create_or_load_account_key
from app.engine.tls_manager import stage_tls_bundle
from app.engine.updater import UpdateWatcher


def test_acme_account_key_uses_atomic_write(tmp_path: Path) -> None:
    """Verifies that ACME account key creation delegates to atomic_write_file with 0600 mode."""
    key_path = tmp_path / "account_key.pem"
    with patch("app.engine.acme_client.atomic_write_file") as mock_atomic_write:
        _ = create_or_load_account_key(key_path)
        mock_atomic_write.assert_called_once()
        assert mock_atomic_write.call_args[0][0] == key_path
        assert mock_atomic_write.call_args[1].get("mode") == 0o600
        assert mock_atomic_write.call_args[1].get("make_backup") is False


def test_updater_acknowledge_post_update_uses_atomic_write(tmp_path: Path) -> None:
    """Verifies that post-update summary acknowledgment persists data atomically."""
    updater = UpdateWatcher(
        repo_url="https://github.com/theStygianArchitect/palworld_server_service",
        repo_dir=tmp_path,
    )
    summary_file = tmp_path / "post_update.json"
    updater.post_update_file = summary_file
    summary_file.write_text(json.dumps({"version": "0.5.0", "acknowledged": False}))

    with patch("app.engine.updater.atomic_write_file") as mock_atomic_write:
        res = updater.acknowledge_last_update()
        assert res is True
        mock_atomic_write.assert_called_once()
        assert mock_atomic_write.call_args[0][0] == summary_file
        saved_content = mock_atomic_write.call_args[0][1]
        data = json.loads(saved_content)
        assert data["acknowledged"] is True


@pytest.mark.asyncio
async def test_updater_lock_uses_atomic_write(tmp_path: Path) -> None:
    """Verifies that apply_update initializes concurrency lock using atomic_write_file."""
    updater = UpdateWatcher(
        repo_url="https://github.com/theStygianArchitect/palworld_server_service",
        repo_dir=tmp_path,
        command_runner=lambda script, branch: None,
    )
    updater.lock_file = tmp_path / ".palworld_update.lock"
    # Create a mock deploy script
    deploy_script = tmp_path / "scripts" / "deploy.sh"
    deploy_script.parent.mkdir(parents=True, exist_ok=True)
    deploy_script.write_text("#!/bin/bash\nexit 0\n")

    with patch("app.engine.updater.atomic_write_file") as mock_atomic_write, \
         patch.object(updater, "resolve_deploy_script", return_value=deploy_script):

        _ = await updater.apply_update(branch="main")
        mock_atomic_write.assert_called_once()
        assert mock_atomic_write.call_args[0][0] == updater.lock_file
        assert "operation=portal_update" in mock_atomic_write.call_args[0][1]


def test_admin_credential_export_uses_atomic_write(tmp_path: Path) -> None:
    """Verifies that out-of-band admin credential export writes credentials atomically."""
    export_path = tmp_path / "initial_admin_credential.txt"

    with patch("app.database.auth.atomic_write_file") as mock_atomic_write:
        _export_credentials_to_file(export_path, "super_secret_password")
        mock_atomic_write.assert_called_once()
        assert mock_atomic_write.call_args[0][0] == export_path
        assert mock_atomic_write.call_args[1].get("mode") == 0o600
        assert "Initial Administrator Credentials" in mock_atomic_write.call_args[0][1]
        assert "super_secret_password" in mock_atomic_write.call_args[0][1]


def test_stage_tls_bundle_creates_files(tmp_path: Path) -> None:
    """Verifies that stage_tls_bundle writes fullchain and privkey atomically."""
    cert_dir = tmp_path / "certs"
    fullchain, privkey = stage_tls_bundle(b"dummy_cert", b"dummy_key", stage_dir=cert_dir)
    assert fullchain.is_file()
    assert privkey.is_file()
    assert fullchain.read_bytes() == b"dummy_cert"
    assert privkey.read_bytes() == b"dummy_key"
