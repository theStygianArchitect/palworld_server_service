"""Unit tests for TLS and SSL configuration and certificate inspection."""

from __future__ import annotations

from pathlib import Path

import certifi
import pytest

from app.core.config import (
    AppSettings,
    inspect_certificate,
    resolve_ssl_paths,
)


def test_app_settings_ssl_defaults() -> None:
    """Tests default SSL attributes on AppSettings."""
    settings = AppSettings()
    assert not settings.ssl_enabled
    assert settings.ssl_cert_path is None
    assert settings.ssl_key_path is None
    assert settings.ssl_port == 8443
    assert settings.ssl_auto_detect is True
    assert settings.letsencrypt_email is None


def test_app_settings_ssl_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests environment variable overrides for SSL settings."""
    monkeypatch.setenv("PALWORLD_SSL_ENABLED", "true")
    monkeypatch.setenv("PALWORLD_SSL_CERT_PATH", "/custom/cert.pem")
    monkeypatch.setenv("PALWORLD_SSL_KEY_PATH", "/custom/key.pem")
    monkeypatch.setenv("PALWORLD_SSL_PORT", "443")
    monkeypatch.setenv("PALWORLD_SSL_AUTO_DETECT", "false")
    monkeypatch.setenv("PALWORLD_LETSENCRYPT_EMAIL", "admin@example.com")

    settings = AppSettings()
    assert settings.ssl_enabled is True
    assert settings.ssl_cert_path == "/custom/cert.pem"
    assert settings.ssl_key_path == "/custom/key.pem"
    assert settings.ssl_port == 443
    assert settings.ssl_auto_detect is False
    assert settings.letsencrypt_email == "admin@example.com"


def test_inspect_certificate_nonexistent() -> None:
    """Tests inspect_certificate with a non-existent file."""
    result = inspect_certificate("/nonexistent/path/cert.pem")
    assert result is None


def test_inspect_certificate_corrupted(tmp_path: Path) -> None:
    """Tests inspect_certificate with invalid non-PEM content."""
    corrupt_file = tmp_path / "corrupt.pem"
    corrupt_file.write_text("NOT A VALID CERTIFICATE DATA", encoding="utf-8")
    result = inspect_certificate(corrupt_file)
    assert result is None


def test_inspect_certificate_valid_certifi(tmp_path: Path) -> None:
    """Tests inspect_certificate using a valid root CA certificate from certifi."""
    with open(certifi.where(), encoding="utf-8") as f:
        bundle = f.read()

    first_cert = bundle.split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"
    cert_file = tmp_path / "valid_cert.pem"
    cert_file.write_text(first_cert, encoding="utf-8")

    info = inspect_certificate(cert_file)
    assert info is not None
    assert info.subject != "Unknown"
    assert info.issuer != "Unknown"
    assert "T" in info.valid_from
    assert "T" in info.expires_at
    assert isinstance(info.days_remaining, int)
    assert isinstance(info.is_expired, bool)


def test_resolve_ssl_paths_none_when_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests resolve_ssl_paths returns None when no certificate candidates exist."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    settings = AppSettings(
        ssl_enabled=False,
        ssl_auto_detect=False,
        ssl_cert_path=None,
        ssl_key_path=None,
    )
    assert resolve_ssl_paths(settings) is None
