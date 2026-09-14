"""Comprehensive unit and resilience tests for the Native Python TLS Certificate Engine."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,missing-class-docstring,too-few-public-methods,duplicate-code

from __future__ import annotations

import stat
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from app.engine.tls_manager import (
    TLSProvisionMode,
    TLSProvisionResult,
    certificate_to_pem,
    clean_domain_name,
    clear_duckdns_txt_record,
    extract_subdomain,
    generate_csr,
    generate_private_key,
    generate_self_signed_certificate,
    get_tls_certificate_status,
    main,
    private_key_to_pem,
    provision_tls_certificates,
    set_duckdns_txt_record,
    stage_tls_bundle,
    sync_duckdns_ip,
)


def test_generate_private_key_properties():
    key = generate_private_key(key_size=2048)
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.key_size == 2048


def test_clean_domain_name_and_extract_subdomain():
    assert clean_domain_name("https://test.duckdns.org:8080/") == "test.duckdns.org"
    assert extract_subdomain("https://test.duckdns.org:8080/") == "test"
    assert clean_domain_name("myserver.duckdns.org") == "myserver.duckdns.org"
    assert extract_subdomain("myserver.duckdns.org") == "myserver"
    assert clean_domain_name("custom.domain.com") == "custom.domain.com"
    assert extract_subdomain("custom.domain.com") == "custom.domain.com"


def test_generate_csr_contains_domain_and_san():
    domain = "test.duckdns.org"
    key = generate_private_key()
    csr = generate_csr(domain, key)

    # Check subject CN
    subject_cns = [attr.value for attr in csr.subject if attr.oid == NameOID.COMMON_NAME]
    assert domain in subject_cns

    # Check SAN
    ext = csr.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    sans = ext.value.get_values_for_type(x509.DNSName)
    assert domain in sans


def test_generate_self_signed_certificate():
    domain = "test.duckdns.org"
    key = generate_private_key()
    cert = generate_self_signed_certificate(key, domain, days_valid=30)

    assert cert.public_key().public_numbers() == key.public_key().public_numbers()

    ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    sans = ext.value.get_values_for_type(x509.DNSName)
    assert domain in sans

    eku = cert.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE)
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku.value

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    not_before = (
        cert.not_valid_before_utc.replace(tzinfo=None)
        if hasattr(cert, "not_valid_before_utc")
        else cert.not_valid_before
    )
    not_after = (
        cert.not_valid_after_utc.replace(tzinfo=None) if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after
    )

    assert not_before <= now
    assert (not_after - not_before).days == 30


def test_stage_tls_bundle_atomic_writes_and_modes(tmp_path: Path):
    cert_pem = b"dummy_cert"
    key_pem = b"dummy_key"

    fullchain, privkey = stage_tls_bundle(cert_pem, key_pem, stage_dir=tmp_path)

    assert fullchain.exists()
    assert privkey.exists()

    assert fullchain.read_bytes() == cert_pem
    assert privkey.read_bytes() == key_pem

    assert stat.S_IMODE(fullchain.stat().st_mode) & 0o777 in (0o644, 0o666)
    assert stat.S_IMODE(privkey.stat().st_mode) & 0o777 in (0o600, 0o666)


def test_get_tls_certificate_status_with_valid_and_missing_certs(tmp_path: Path):
    domain = "test.duckdns.org"
    # Missing certs
    status_missing = get_tls_certificate_status(stage_dir=tmp_path, domain=domain)
    assert not status_missing.is_valid
    assert status_missing.error_message == "Certificate files missing"

    # Valid cert
    key = generate_private_key()
    cert = generate_self_signed_certificate(key, domain, days_valid=30)

    stage_tls_bundle(certificate_to_pem(cert), private_key_to_pem(key), stage_dir=tmp_path)

    status_valid = get_tls_certificate_status(stage_dir=tmp_path, domain=domain)
    assert status_valid.is_valid
    assert status_valid.days_remaining in (29, 30)
    assert status_valid.is_self_signed
    assert domain in status_valid.subject_alt_names


@pytest.mark.asyncio
async def test_duckdns_txt_record_and_ip_sync_mock(monkeypatch):
    class MockResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code != 200:
                raise httpx.HTTPStatusError("error", request=None, response=self)

    async def mock_get(self, url, **kwargs):
        if "txt=good" in url or "clear=true" in url or "ip=1.1.1.1" in url:
            return MockResponse("OK")
        if "txt=bad" in url:
            return MockResponse("KO")
        if "txt=error" in url:
            raise httpx.RequestError("network error")
        return MockResponse("OK")

    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    # set_duckdns_txt_record
    assert await set_duckdns_txt_record("test.duckdns.org", "token", "good") is True
    assert await set_duckdns_txt_record("test.duckdns.org", "token", "bad") is False
    assert await set_duckdns_txt_record("test.duckdns.org", "token", "error") is False

    # clear_duckdns_txt_record
    assert await clear_duckdns_txt_record("test.duckdns.org", "token") is True

    # sync_duckdns_ip
    assert await sync_duckdns_ip("test.duckdns.org", "token", "1.1.1.1") is True


@pytest.mark.asyncio
async def test_provision_tls_certificates_fallback_to_self_signed(tmp_path: Path, monkeypatch):
    domain = "test.duckdns.org"
    result = await provision_tls_certificates(
        domain=domain,
        token="dummy",  # nosec B106 - mock test token
        force=True,
        stage_dir=tmp_path,
    )

    assert result.success is True
    assert result.mode == TLSProvisionMode.SELF_SIGNED_FALLBACK
    assert result.fullchain_path is not None
    assert result.privkey_path is not None
    assert result.fullchain_path.exists()
    assert result.privkey_path.exists()


@pytest.mark.asyncio
async def test_provision_tls_certificates_returns_existing_when_valid(tmp_path: Path):
    domain = "test.duckdns.org"

    # Provision initially
    await provision_tls_certificates(
        domain=domain,
        token="dummy",  # nosec B106 - mock test token
        force=True,
        stage_dir=tmp_path,
    )

    # Call again without force
    result2 = await provision_tls_certificates(
        domain=domain,
        token="dummy",  # nosec B106 - mock test token
        force=False,
        stage_dir=tmp_path,
    )
    assert result2.mode == TLSProvisionMode.EXISTING_VALID
    assert result2.days_remaining in (364, 365)


def test_cli_main_entrypoint(tmp_path: Path, monkeypatch, capsys):
    domain = "test.duckdns.org"

    # Just run the actual code but inside tmp_path? Wait, if we use the actual code it might write to DEFAULT_CERT_DIR.
    # Better to mock stage_dir in the CLI? The CLI doesn't accept stage_dir! It gets it from settings.
    # Let's mock `get_tls_certificate_status` to avoid reading real files in `status`.

    # Status
    with patch("app.engine.tls_manager.get_tls_certificate_status") as mock_status:
        mock_status.return_value.domain = domain
        mock_status.return_value.is_valid = True
        mock_status.return_value.days_remaining = 30
        mock_status.return_value.issuer = "issuer"
        mock_status.return_value.is_self_signed = True
        mock_status.return_value.error_message = None

        assert main(["status", "--domain", domain]) == 0
        captured = capsys.readouterr()
        assert "Valid: True" in captured.out

    # Renew
    with patch("app.engine.tls_manager.provision_tls_certificates", new_callable=AsyncMock) as mock_prov:
        mock_prov.return_value = TLSProvisionResult(
            True, TLSProvisionMode.SELF_SIGNED_FALLBACK, domain, tmp_path / "a", tmp_path / "b", 365, "msg"
        )
        assert main(["renew", "--domain", domain, "--token", "abc"]) == 0
        captured = capsys.readouterr()
        assert "Provision result: True" in captured.out

    # Sync DNS
    with patch("app.engine.tls_manager.sync_duckdns_ip", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = True
        assert main(["sync-dns", "--domain", domain, "--token", "abc", "--ip", "1.1.1.1"]) == 0
        captured = capsys.readouterr()
        assert "Successfully synced DNS" in captured.out
