"""Unit tests for TLS and SSL certificate Pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    TLSCertificateInfo,
    TLSRenewRequest,
    TLSRenewResponse,
    TLSStatusResponse,
)


def test_tls_certificate_info_valid() -> None:
    """Tests creating a valid TLSCertificateInfo instance."""
    cert = TLSCertificateInfo(
        subject="palworld.duckdns.org",
        issuer="Let's Encrypt",
        valid_from="2026-09-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z",
        days_remaining=83,
        is_expired=False,
        san_list=["palworld.duckdns.org", "*.palworld.duckdns.org"],
    )
    assert cert.subject == "palworld.duckdns.org"
    assert cert.issuer == "Let's Encrypt"
    assert cert.days_remaining == 83
    assert not cert.is_expired
    assert len(cert.san_list) == 2


def test_tls_status_response_http_fallback() -> None:
    """Tests TLSStatusResponse under HTTP fallback mode without a certificate."""
    status = TLSStatusResponse(
        enabled=False,
        scheme="http",
        domain="palworld.duckdns.org",
        port=8080,
        certificate=None,
        cert_path=None,
        auto_renew_active=False,
        warning="Running unencrypted plaintext HTTP",
    )
    assert not status.enabled
    assert status.scheme == "http"
    assert status.certificate is None
    assert status.warning == "Running unencrypted plaintext HTTP"


def test_tls_status_response_https_active() -> None:
    """Tests TLSStatusResponse under active HTTPS mode with full certificate data."""
    cert = TLSCertificateInfo(
        subject="palworld.duckdns.org",
        issuer="Let's Encrypt",
        valid_from="2026-09-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z",
        days_remaining=83,
        is_expired=False,
        san_list=["palworld.duckdns.org"],
    )
    status = TLSStatusResponse(
        enabled=True,
        scheme="https",
        domain="palworld.duckdns.org",
        port=8443,
        certificate=cert,
        cert_path="/var/lib/palmanager/certs/fullchain.pem",
        auto_renew_active=True,
        warning=None,
    )
    assert status.enabled
    assert status.scheme == "https"
    assert status.port == 8443
    assert status.certificate is not None
    assert status.certificate.days_remaining == 83
    assert status.auto_renew_active


def test_tls_renew_response_validation() -> None:
    """Tests TLSRenewResponse validation and serialization."""
    resp = TLSRenewResponse(
        status="success",
        message="Certificate successfully renewed and staged.",
        triggered_at="2026-09-09T03:40:00Z",
    )
    assert resp.status == "success"
    assert "successfully renewed" in resp.message

    with pytest.raises(ValidationError):
        TLSRenewResponse(  # type: ignore[call-arg]
            status="invalid_status",  # type: ignore[arg-type]
            message="Test",
            triggered_at="2026-09-09T03:40:00Z",
        )


def test_tls_renew_request_defaults_and_custom() -> None:
    """Tests TLSRenewRequest default and explicit force values."""
    req_default = TLSRenewRequest()
    assert not req_default.force

    req_forced = TLSRenewRequest(force=True)
    assert req_forced.force
