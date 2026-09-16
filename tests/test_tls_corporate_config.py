"""Regression tests for corporate certificate discovery, precedence, and TLS configuration."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import AppSettings, resolve_ssl_paths
from app.engine.tls_manager import (
    certificate_to_pem,
    generate_private_key,
    generate_self_signed_certificate,
    private_key_to_pem,
)


@pytest.fixture
def valid_cert_and_key() -> tuple[bytes, bytes]:
    """Generates a valid self-signed certificate and private key in PEM format."""
    key = generate_private_key()
    cert = generate_self_signed_certificate(key, "localhost")
    return certificate_to_pem(cert), private_key_to_pem(key)


@pytest.fixture
def another_cert_and_key() -> tuple[bytes, bytes]:
    """Generates a distinct second key pair for mismatch testing."""
    key = generate_private_key()
    cert = generate_self_signed_certificate(key, "localhost")
    return certificate_to_pem(cert), private_key_to_pem(key)


@pytest.mark.parametrize(
    ("cert_name", "key_name"),
    [
        ("tls.crt", "tls.key"),
        ("fullchain.pem", "privkey.pem"),
        ("cert.pem", "key.pem"),
        ("server.crt", "server.key"),
    ],
)
def test_resolve_ssl_paths_from_cert_dir(
    tmp_path: Path,
    valid_cert_and_key: tuple[bytes, bytes],
    cert_name: str,
    key_name: str,
) -> None:
    """Verifies that resolve_ssl_paths discovers common cert/key naming conventions in ssl_cert_dir."""
    cert_pem, key_pem = valid_cert_and_key
    cert_dir = tmp_path / "corporate_certs"
    cert_dir.mkdir()

    cert_file = cert_dir / cert_name
    key_file = cert_dir / key_name
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(key_pem)

    settings = AppSettings(
        ssl_cert_dir=str(cert_dir),
        ssl_enabled=True,
        ssl_auto_detect=False,
    )

    resolved = resolve_ssl_paths(settings)
    assert resolved is not None
    res_cert, res_key = resolved
    assert res_cert == cert_file.resolve()
    assert res_key == key_file.resolve()


def test_resolve_ssl_paths_explicit_overrides_cert_dir(
    tmp_path: Path,
    valid_cert_and_key: tuple[bytes, bytes],
) -> None:
    """Verifies that explicit ssl_cert_path and ssl_key_path take precedence over ssl_cert_dir."""
    cert_pem, key_pem = valid_cert_and_key
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    # Place one pair in dir1
    cert1 = dir1 / "tls.crt"
    key1 = dir1 / "tls.key"
    cert1.write_bytes(cert_pem)
    key1.write_bytes(key_pem)

    # Place another pair in dir2
    cert2 = dir2 / "explicit.crt"
    key2 = dir2 / "explicit.key"
    cert2.write_bytes(cert_pem)
    key2.write_bytes(key_pem)

    settings = AppSettings(
        ssl_cert_dir=str(dir1),
        ssl_cert_path=str(cert2),
        ssl_key_path=str(key2),
        ssl_enabled=True,
    )

    resolved = resolve_ssl_paths(settings)
    assert resolved is not None
    res_cert, res_key = resolved
    assert res_cert == cert2.resolve()
    assert res_key == key2.resolve()


def test_resolve_ssl_paths_rejects_cryptographic_mismatch(
    tmp_path: Path,
    valid_cert_and_key: tuple[bytes, bytes],
    another_cert_and_key: tuple[bytes, bytes],
) -> None:
    """Verifies that a certificate paired with a non-matching private key is rejected."""
    cert_pem, _ = valid_cert_and_key
    _, wrong_key_pem = another_cert_and_key

    cert_dir = tmp_path / "mismatched"
    cert_dir.mkdir()
    (cert_dir / "tls.crt").write_bytes(cert_pem)
    (cert_dir / "tls.key").write_bytes(wrong_key_pem)

    settings = AppSettings(
        ssl_cert_dir=str(cert_dir),
        ssl_enabled=True,
        ssl_auto_detect=False,
    )

    resolved = resolve_ssl_paths(settings)
    assert resolved is None


def test_settings_auto_renew_and_cert_mode_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies default settings and environment variable aliases for auto-renewal and cert mode."""
    # Defaults
    settings = AppSettings()
    assert hasattr(settings, "ssl_auto_renew")
    assert settings.ssl_auto_renew is False
    assert hasattr(settings, "ssl_cert_mode")
    assert settings.ssl_cert_mode == "letsencrypt"

    # Environment overrides
    monkeypatch.setenv("PALWORLD_SSL_AUTO_RENEW", "true")
    monkeypatch.setenv("PALWORLD_SSL_CERT_MODE", "self_signed")
    settings_custom = AppSettings()
    assert settings_custom.ssl_auto_renew is True
    assert settings_custom.ssl_cert_mode == "self_signed"
