"""Unit and resilience tests for the in-process certificate renewal background scheduler."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import AppSettings
from app.engine.tls_manager import TLSCertificateStatus, TLSProvisionMode, TLSProvisionResult
from app.engine.tls_scheduler import certificate_renewal_scheduler


@pytest.mark.asyncio
async def test_scheduler_sleeps_when_cert_is_valid() -> None:
    """Verifies that the scheduler calculates sleep duration and does not renew if cert has plenty of days left."""
    not_now = datetime.now(timezone.utc)
    fake_status = TLSCertificateStatus(
        is_valid=True,
        domain="test.duckdns.org",
        issuer="Let's Encrypt",
        subject_alt_names=["test.duckdns.org"],
        not_before=not_now,
        not_after=not_now + timedelta(days=60),
        days_remaining=60,
        fullchain_path=None,
        privkey_path=None,
        is_self_signed=False,
    )

    settings = AppSettings(
        ssl_auto_renew=True,
        ssl_cert_mode="letsencrypt",
        duckdns_domain="test.duckdns.org",
        duckdns_token="dummy_token",  # nosec B106 - mock test token
    )

    with patch("app.engine.tls_scheduler.get_settings", return_value=settings), \
         patch("app.engine.tls_scheduler.get_tls_certificate_status", return_value=fake_status), \
         patch("app.engine.tls_scheduler.provision_tls_certificates", new_callable=AsyncMock) as mock_provision, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        # Let the loop run once and raise CancelledError to break out
        mock_sleep.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await certificate_renewal_scheduler()

        # Should NOT have attempted renewal
        mock_provision.assert_not_called()
        mock_sleep.assert_called_once()
        # Days remaining is 60, threshold is 30, so (60-30)*86400 or capped at 86400
        slept_duration = mock_sleep.call_args[0][0]
        assert slept_duration > 0


@pytest.mark.asyncio
async def test_scheduler_triggers_renewal_when_nearing_expiration() -> None:
    """Verifies that the scheduler initiates certificate provisioning and restarts service when days <= 30."""
    not_now = datetime.now(timezone.utc)
    fake_status = TLSCertificateStatus(
        is_valid=True,
        domain="test.duckdns.org",
        issuer="Let's Encrypt",
        subject_alt_names=["test.duckdns.org"],
        not_before=not_now,
        not_after=not_now + timedelta(days=10),
        days_remaining=10,
        fullchain_path=None,
        privkey_path=None,
        is_self_signed=False,
    )

    settings = AppSettings(
        ssl_auto_renew=True,
        ssl_cert_mode="letsencrypt",
        duckdns_domain="test.duckdns.org",
        duckdns_token="dummy_token",  # nosec B106 - mock test token
    )

    provision_res = TLSProvisionResult(
        success=True,
        mode=TLSProvisionMode.ACME_LETSENCRYPT,
        domain="test.duckdns.org",
        fullchain_path=None,
        privkey_path=None,
        days_remaining=90,
        message="Renewed",
    )

    provision_mock = AsyncMock(return_value=provision_res)
    with (
        patch("app.engine.tls_scheduler.get_settings", return_value=settings),
        patch("app.engine.tls_scheduler.get_tls_certificate_status", return_value=fake_status),
        patch("app.engine.tls_scheduler.provision_tls_certificates", provision_mock),
        patch("app.engine.tls_scheduler.dispatch_manager_service_restart") as mock_restart,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):

        mock_sleep.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await certificate_renewal_scheduler()

        provision_mock.assert_called_once()
        mock_restart.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_respects_auto_renew_disabled() -> None:
    """Verifies that the scheduler pauses and does not trigger provisioning when ssl_auto_renew is False."""
    settings = AppSettings(
        ssl_auto_renew=False,
        ssl_cert_mode="letsencrypt",
        duckdns_domain="test.duckdns.org",
    )

    with patch("app.engine.tls_scheduler.get_settings", return_value=settings), \
         patch("app.engine.tls_scheduler.provision_tls_certificates", new_callable=AsyncMock) as mock_provision, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        mock_sleep.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await certificate_renewal_scheduler()

        mock_provision.assert_not_called()
        mock_sleep.assert_called_once_with(3600)


@pytest.mark.asyncio
async def test_scheduler_skips_when_cert_mode_custom() -> None:
    """Verifies that custom certificates are never auto-renewed by the background scheduler."""
    settings = AppSettings(
        ssl_auto_renew=True,
        ssl_cert_mode="custom",
    )

    with patch("app.engine.tls_scheduler.get_settings", return_value=settings), \
         patch("app.engine.tls_scheduler.provision_tls_certificates", new_callable=AsyncMock) as mock_provision, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        mock_sleep.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await certificate_renewal_scheduler()

        mock_provision.assert_not_called()
        mock_sleep.assert_called_once_with(86400)
