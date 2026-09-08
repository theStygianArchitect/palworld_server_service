"""Unit tests for configuration loaders and PalWorldSettings source resolution."""
# pylint: disable=missing-function-docstring
# Rationale: Pytest test function names are self-descriptive and documented via assertions.

import os
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import (
    AppSettings,
    PalWorldIniSettingsSource,
    get_settings,
    resolve_admin_credential_export_path,
)

SAMPLE_INI = (
    "[/Script/Pal.PalGameWorldSettings]\n"
    "OptionSettings=(ServerName=\"Test Server Name\",AdminPassword=\"SecureAdminPassword123\","
    "PublicPort=8211,RCONPort=25575,RESTAPIPort=8212,RESTAPIEnabled=True)\n"
)


def test_app_settings_defaults():
    settings = AppSettings(ini_path="/non/existent.ini")
    assert settings.PublicPort == 8211
    assert settings.RESTAPIPort == 8212
    assert settings.service_name == "palworld.service"


def test_app_settings_custom_ini_source():
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(SAMPLE_INI)
        temp_path = f.name

    try:
        source = PalWorldIniSettingsSource(AppSettings, ini_path=temp_path)
        data = source()
        assert data["ServerName"] == "Test Server Name"
        assert data["AdminPassword"] == "SecureAdminPassword123"
        assert data["PublicPort"] == 8211
        assert data["RCONPort"] == 25575
        assert data["RESTAPIPort"] == 8212

        # Test non-existent file
        source_missing = PalWorldIniSettingsSource(AppSettings, ini_path="/non/existent/path.ini")
        assert not source_missing()
        assert source_missing.get_field_value(None, "ServerName") == (None, "ServerName", False)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_get_settings_helper():
    s = get_settings()
    assert isinstance(s, AppSettings)


def test_github_repo_url_default():
    s = AppSettings(ini_path="/non/existent.ini")
    assert s.github_repo_url == "https://github.com/theStygianArchitect/palworld_server_service"


def test_github_repo_url_env_override(monkeypatch):
    monkeypatch.setenv("PALWORLD_GITHUB_REPO_URL", "https://github.com/custom/repo/")
    s = AppSettings(ini_path="/non/existent.ini")
    assert s.github_repo_url == "https://github.com/custom/repo"


def test_github_repo_url_validation():
    with pytest.raises(ValidationError):
        AppSettings(ini_path="/non/existent.ini", github_repo_url="ftp://invalid.url")


def test_admin_credential_export_path_default():
    s = AppSettings(ini_path="/non/existent.ini")
    assert s.admin_credential_export_path is None
    resolved = resolve_admin_credential_export_path(s.admin_credential_export_path)
    assert isinstance(resolved, Path)
    assert resolved.name == "initial_admin_credential.txt"


def test_admin_credential_export_path_custom(tmp_path: Path, monkeypatch):
    custom_target = str(tmp_path / "custom_creds.txt")
    monkeypatch.setenv("PALWORLD_ADMIN_CREDENTIAL_EXPORT_PATH", custom_target)
    s = AppSettings(ini_path="/non/existent.ini")
    assert s.admin_credential_export_path == custom_target
    resolved = resolve_admin_credential_export_path(s.admin_credential_export_path)
    assert resolved == Path(custom_target).resolve()
    assert resolved.parent.exists()
