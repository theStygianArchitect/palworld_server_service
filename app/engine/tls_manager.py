"""Core TLS Engine Specialist: Manages TLS certificates, ACME provision, and self-signed fallbacks.

Provides tools for generating, checking, and updating X.509 certificates and DuckDNS integration.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from app.core.atomic_io import atomic_write_file

logger = logging.getLogger(__name__)


DEFAULT_HTTP_TIMEOUT = 15.0
DEFAULT_CERT_DIR = "/var/lib/palmanager/certs"
RENEWAL_THRESHOLD_DAYS = 30


class TLSProvisionMode(str, Enum):
    """Modes for provisioning TLS certificates."""

    ACME_LETSENCRYPT = "letsencrypt"
    SELF_SIGNED_FALLBACK = "self_signed"
    EXISTING_VALID = "existing"


# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class TLSCertificateStatus:
    """Represents the current status of a TLS certificate."""

    is_valid: bool
    domain: str
    issuer: str
    subject_alt_names: list[str]
    not_before: datetime | None
    not_after: datetime | None
    days_remaining: int
    fullchain_path: Path | None
    privkey_path: Path | None
    is_self_signed: bool
    error_message: str | None = None


# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class TLSProvisionResult:
    """Represents the outcome of a TLS provisioning attempt."""

    success: bool
    mode: TLSProvisionMode
    domain: str
    fullchain_path: Path | None
    privkey_path: Path | None
    days_remaining: int
    message: str
    error: str | None = None


def generate_private_key(key_size: int = 2048) -> rsa.RSAPrivateKey:
    """Generate a new RSA private key.

    Args:
        key_size: The size of the RSA key in bits.

    Returns:
        A new RSAPrivateKey instance.
    """
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
    )


def private_key_to_pem(private_key: rsa.RSAPrivateKey) -> bytes:
    """Serialize an RSA private key to PEM format.

    Args:
        private_key: The RSAPrivateKey to serialize.

    Returns:
        The PEM encoded private key as bytes.
    """
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def certificate_to_pem(cert: x509.Certificate) -> bytes:
    """Serialize an X.509 certificate to PEM format.

    Args:
        cert: The X.509 certificate to serialize.

    Returns:
        The PEM encoded certificate as bytes.
    """
    return cert.public_bytes(serialization.Encoding.PEM)


def csr_to_pem(csr: x509.CertificateSigningRequest) -> bytes:
    """Serialize a Certificate Signing Request to PEM format.

    Args:
        csr: The Certificate Signing Request to serialize.

    Returns:
        The PEM encoded CSR as bytes.
    """
    return csr.public_bytes(serialization.Encoding.PEM)


def clean_domain_name(domain: str) -> str:
    """Normalize a domain by stripping protocol, trailing slashes, and ports.

    Args:
        domain: The raw domain string.

    Returns:
        The cleaned domain string.
    """
    cleaned = domain.strip().lower()
    for prefix in ("https://", "http://"):
        cleaned = cleaned.removeprefix(prefix)

    if "/" in cleaned:
        cleaned = cleaned.split("/", 1)[0]
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[0]

    return cleaned


def extract_subdomain(domain: str) -> str:
    """Extract the DuckDNS subdomain if applicable.

    Args:
        domain: The full domain name.

    Returns:
        The base subdomain if duckdns.org, otherwise the clean domain.
    """
    return clean_domain_name(domain).removesuffix(".duckdns.org")


def generate_csr(domain: str, private_key: rsa.RSAPrivateKey) -> x509.CertificateSigningRequest:
    """Generate a Certificate Signing Request (CSR) for a domain.

    Args:
        domain: The domain name to secure.
        private_key: The RSA private key to sign the CSR.

    Returns:
        A new CertificateSigningRequest instance.
    """
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)]))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(domain)]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )


def generate_self_signed_certificate(
    private_key: rsa.RSAPrivateKey, domain: str, days_valid: int = 365
) -> x509.Certificate:
    """Generate a self-signed X.509 certificate.

    Args:
        private_key: The RSA private key to sign the certificate.
        domain: The domain name.
        days_valid: Number of days the certificate is valid.

    Returns:
        A self-signed X.509 Certificate instance.
    """
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.now(timezone.utc)

    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=days_valid))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(domain)]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )


async def set_duckdns_txt_record(
    domain: str, token: str, txt_record: str, timeout: float = DEFAULT_HTTP_TIMEOUT
) -> bool:
    """Set a TXT record for a DuckDNS domain.

    Args:
        domain: The full domain name.
        token: DuckDNS API token.
        txt_record: The TXT record value.
        timeout: Request timeout.

    Returns:
        True if the update was successful, False otherwise.
    """
    subdomain = extract_subdomain(domain)
    url = f"https://www.duckdns.org/update?domains={subdomain}&token={token}&txt={txt_record}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text.startswith("OK")
    except httpx.RequestError as exc:
        logger.exception("Failed to set DuckDNS TXT record: %s", exc)
        return False
    except httpx.HTTPStatusError as exc:
        logger.exception("HTTP error setting DuckDNS TXT record: %s", exc)
        return False


async def clear_duckdns_txt_record(domain: str, token: str, timeout: float = DEFAULT_HTTP_TIMEOUT) -> bool:
    """Clear the TXT record for a DuckDNS domain.

    Args:
        domain: The full domain name.
        token: DuckDNS API token.
        timeout: Request timeout.

    Returns:
        True if the clear was successful, False otherwise.
    """
    subdomain = extract_subdomain(domain)
    url = f"https://www.duckdns.org/update?domains={subdomain}&token={token}&clear=true"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text.startswith("OK")
    except httpx.RequestError as exc:
        logger.exception("Failed to clear DuckDNS TXT record: %s", exc)
        return False
    except httpx.HTTPStatusError as exc:
        logger.exception("HTTP error clearing DuckDNS TXT record: %s", exc)
        return False


async def sync_duckdns_ip(domain: str, token: str, ip: str = "", timeout: float = DEFAULT_HTTP_TIMEOUT) -> bool:
    """Sync the IP address for a DuckDNS domain.

    Args:
        domain: The full domain name.
        token: DuckDNS API token.
        ip: IP address to set (leave empty for auto-detection).
        timeout: Request timeout.

    Returns:
        True if the sync was successful, False otherwise.
    """
    subdomain = extract_subdomain(domain)
    url = f"https://www.duckdns.org/update?domains={subdomain}&token={token}&ip={ip}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text.startswith("OK")
    except httpx.RequestError as exc:
        logger.exception("Failed to sync DuckDNS IP: %s", exc)
        return False
    except httpx.HTTPStatusError as exc:
        logger.exception("HTTP error syncing DuckDNS IP: %s", exc)
        return False


def stage_tls_bundle(cert_pem: bytes, key_pem: bytes, stage_dir: Path | None = None) -> tuple[Path, Path]:
    """Atomically write the TLS fullchain and private key to disk.

    Args:
        cert_pem: The PEM encoded certificate bundle.
        key_pem: The PEM encoded private key.
        stage_dir: The directory to write the files to.

    Returns:
        A tuple containing the paths to the (fullchain, privkey).
    """
    if stage_dir is None:
        try:
            # pylint: disable=import-outside-toplevel
            from app.core.config import get_settings

            stage_dir = Path(get_settings().cert_dir)
        except ImportError as err:
            logger.debug("ImportError loading settings in stage_tls_bundle: %s", err)
            stage_dir = Path(DEFAULT_CERT_DIR)
        except AttributeError as err:
            logger.debug("AttributeError loading settings in stage_tls_bundle: %s", err)
            stage_dir = Path(DEFAULT_CERT_DIR)
        except OSError as err:
            logger.debug("OSError loading settings in stage_tls_bundle: %s", err)
            stage_dir = Path(DEFAULT_CERT_DIR)

    stage_dir.mkdir(parents=True, exist_ok=True)
    fullchain_path = stage_dir / "fullchain.pem"
    privkey_path = stage_dir / "privkey.pem"

    atomic_write_file(fullchain_path, cert_pem, mode=0o644, make_backup=False)
    atomic_write_file(privkey_path, key_pem, mode=0o600, make_backup=False)

    return fullchain_path, privkey_path


# pylint: disable=too-many-locals
def get_tls_certificate_status(stage_dir: Path | None = None, domain: str | None = None) -> TLSCertificateStatus:
    """Read the staged TLS certificate and return its status.

    Args:
        stage_dir: Directory containing the certificates.
        domain: Expected domain name.

    Returns:
        A TLSCertificateStatus representing the certificate health.
    """
    if stage_dir is None:
        try:
            # pylint: disable=import-outside-toplevel
            from app.core.config import get_settings

            stage_dir = Path(get_settings().cert_dir)
        except ImportError as err:
            logger.debug("ImportError loading settings in get_tls_certificate_status: %s", err)
            stage_dir = Path(DEFAULT_CERT_DIR)
        except AttributeError as err:
            logger.debug("AttributeError loading settings in get_tls_certificate_status: %s", err)
            stage_dir = Path(DEFAULT_CERT_DIR)
        except OSError as err:
            logger.debug("OSError loading settings in get_tls_certificate_status: %s", err)
            stage_dir = Path(DEFAULT_CERT_DIR)

    fullchain_path = stage_dir / "fullchain.pem"
    privkey_path = stage_dir / "privkey.pem"

    if not fullchain_path.exists() or not privkey_path.exists():
        return TLSCertificateStatus(
            is_valid=False,
            domain=domain or "unknown",
            issuer="unknown",
            subject_alt_names=[],
            not_before=None,
            not_after=None,
            days_remaining=0,
            fullchain_path=fullchain_path,
            privkey_path=privkey_path,
            is_self_signed=False,
            error_message="Certificate files missing",
        )

    try:
        cert_data = fullchain_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_data)
    except ValueError as exc:
        logger.exception("Failed to load certificate: %s", exc)
        return TLSCertificateStatus(
            is_valid=False,
            domain=domain or "unknown",
            issuer="unknown",
            subject_alt_names=[],
            not_before=None,
            not_after=None,
            days_remaining=0,
            fullchain_path=fullchain_path,
            privkey_path=privkey_path,
            is_self_signed=False,
            error_message=f"Failed to load certificate: {exc}",
        )

    issuer_cn = "unknown"
    for attr in cert.issuer:
        if attr.oid == NameOID.COMMON_NAME:
            issuer_cn = str(attr.value)
            break

    subject_cn = "unknown"
    for attr in cert.subject:
        if attr.oid == NameOID.COMMON_NAME:
            subject_cn = str(attr.value)
            break

    is_self_signed = issuer_cn == subject_cn

    sans: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        sans = [str(x) for x in ext.value.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound as exc:
        logger.debug("Extension not found: %s", exc)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    not_before = (
        cert.not_valid_before_utc.replace(tzinfo=None)
        if hasattr(cert, "not_valid_before_utc")
        else cert.not_valid_before
    )
    not_after = (
        cert.not_valid_after_utc.replace(tzinfo=None) if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after
    )

    delta = not_after - now
    days_remaining = max(0, delta.days)
    is_valid = days_remaining > 0 and not_before <= now

    actual_domain = domain or subject_cn

    return TLSCertificateStatus(
        is_valid=is_valid,
        domain=actual_domain,
        issuer=issuer_cn,
        subject_alt_names=sans,
        not_before=not_before,
        not_after=not_after,
        days_remaining=days_remaining,
        fullchain_path=fullchain_path,
        privkey_path=privkey_path,
        is_self_signed=is_self_signed,
        error_message=None if is_valid else "Certificate expired or not yet valid",
    )


async def provision_tls_certificates(
    domain: str, token: str, force: bool = False, stage_dir: Path | None = None
) -> TLSProvisionResult:
    """Provision TLS certificates with tiered strategy.

    Tier 1: ACME DNS-01 via Let's Encrypt (requires valid DuckDNS token).
    Tier 2: Self-signed fallback (if ACME fails or token unavailable).

    Args:
        domain: The domain to secure.
        token: DuckDNS API token for DNS-01 challenge and IP sync.
        force: Force renewal even if current certificate is valid.
        stage_dir: Directory to save certificates.

    Returns:
        TLSProvisionResult describing the provisioning outcome.
    """
    status = get_tls_certificate_status(stage_dir=stage_dir, domain=domain)
    if not force and status.is_valid and status.days_remaining > RENEWAL_THRESHOLD_DAYS:
        return TLSProvisionResult(
            success=True,
            mode=TLSProvisionMode.EXISTING_VALID,
            domain=domain,
            fullchain_path=status.fullchain_path,
            privkey_path=status.privkey_path,
            days_remaining=status.days_remaining,
            message="Existing certificate is valid and not near expiry.",
        )

    private_key = generate_private_key()

    # Tier 1: Attempt ACME DNS-01 via Let's Encrypt
    if token and token.strip():
        # Local import to avoid circular dependency
        from app.engine.acme_client import perform_dns01_flow  # pylint: disable=import-outside-toplevel
        logger.info("Attempting ACME DNS-01 certificate provisioning for %s", domain)
        try:
            acme_result = await perform_dns01_flow(
                domain=domain,
                duckdns_token=token,
                private_key=private_key,
            )
            if acme_result.success and acme_result.fullchain_pem:
                key_pem = private_key_to_pem(private_key)
                fullchain, privkey = stage_tls_bundle(
                    acme_result.fullchain_pem, key_pem, stage_dir=stage_dir,
                )
                logger.info("ACME DNS-01 certificate provisioned successfully for %s", domain)
                return TLSProvisionResult(
                    success=True,
                    mode=TLSProvisionMode.ACME_LETSENCRYPT,
                    domain=domain,
                    fullchain_path=fullchain,
                    privkey_path=privkey,
                    days_remaining=90,
                    message="Provisioned Let's Encrypt certificate via ACME DNS-01.",
                )
            logger.warning(
                "ACME DNS-01 failed for %s: %s. Falling back to self-signed.",
                domain,
                acme_result.error,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception("ACME DNS-01 unexpected error for %s. Falling back to self-signed.", domain)
    else:
        logger.info("No DuckDNS token provided. Skipping ACME, using self-signed certificate.")

    # Tier 2: Self-signed fallback
    cert = generate_self_signed_certificate(private_key, domain)
    cert_pem = certificate_to_pem(cert)
    key_pem = private_key_to_pem(private_key)

    fullchain, privkey = stage_tls_bundle(cert_pem, key_pem, stage_dir=stage_dir)

    return TLSProvisionResult(
        success=True,
        mode=TLSProvisionMode.SELF_SIGNED_FALLBACK,
        domain=domain,
        fullchain_path=fullchain,
        privkey_path=privkey,
        days_remaining=365,
        message="Provisioned self-signed fallback certificate.",
    )


def _handle_renew(args: argparse.Namespace) -> int:
    """Handle CLI renewal command."""
    if not args.domain or not args.token:
        print("Error: Domain and token must be configured or provided via CLI.")
        return 1
    result = asyncio.run(provision_tls_certificates(domain=args.domain, token=args.token, force=args.force))
    print(f"Provision result: {result.success} ({result.mode.value}) - {result.message}")
    if result.error:
        print(f"Error: {result.error}")
        return 1
    return 0


def _handle_sync_dns(args: argparse.Namespace) -> int:
    """Handle CLI sync-dns command."""
    success = asyncio.run(sync_duckdns_ip(domain=args.domain, token=args.token, ip=args.ip))
    if success:
        print(f"Successfully synced DNS for {args.domain}")
        return 0
    print(f"Failed to sync DNS for {args.domain}")
    return 1


def _handle_status(args: argparse.Namespace) -> int:
    """Handle CLI status command."""
    status = get_tls_certificate_status(domain=args.domain)
    print(f"Status for {status.domain}:")
    print(f"  Valid: {status.is_valid}")
    print(f"  Days remaining: {status.days_remaining}")
    print(f"  Issuer: {status.issuer}")
    print(f"  Self-signed: {status.is_self_signed}")
    if status.error_message:
        print(f"  Error: {status.error_message}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for TLS management.

    Args:
        argv: Command-line arguments.

    Returns:
        Exit code.
    """
    default_domain = ""
    default_token: str = ""  # nosec B105 - empty string fallback for CLI token
    try:
        # pylint: disable=import-outside-toplevel
        from app.core.config import get_settings

        cfg = get_settings()
        default_domain = cfg.duckdns_domain or ""
        default_token = cfg.duckdns_token or ""
    except ImportError as err:
        logger.debug("ImportError loading settings for CLI: %s", err)
    except AttributeError as err:
        logger.debug("AttributeError loading settings for CLI: %s", err)
    except OSError as err:
        logger.debug("OSError loading settings for CLI: %s", err)

    parser = argparse.ArgumentParser(description="TLS Manager CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    renew_parser = subparsers.add_parser("renew", help="Provision/renew cert")
    renew_parser.add_argument("--domain", default=default_domain, help="Domain name")
    renew_parser.add_argument("--token", default=default_token, help="API token")
    renew_parser.add_argument("--force", action="store_true", help="Force renewal")

    sync_parser = subparsers.add_parser("sync-dns", help="Call sync_duckdns_ip")
    sync_parser.add_argument("--domain", default=default_domain, help="Domain name")
    sync_parser.add_argument("--token", default=default_token, help="API token")
    sync_parser.add_argument("--ip", default="", help="IP address to sync")

    status_parser = subparsers.add_parser("status", help="Print certificate health status")
    status_parser.add_argument("--domain", default=default_domain, help="Domain name")

    args = parser.parse_args(argv)

    if args.command == "renew":
        return _handle_renew(args)
    if args.command == "sync-dns":
        return _handle_sync_dns(args)
    if args.command == "status":
        return _handle_status(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
