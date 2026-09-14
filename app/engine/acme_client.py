"""ACME client for DNS-01 challenge."""
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,broad-exception-caught

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app.engine.duckdns import clear_duckdns_txt_record, set_duckdns_txt_record

logger = logging.getLogger(__name__)

DEFAULT_ACME_DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
DEFAULT_ACCOUNT_KEY_PATH = Path("/var/lib/palmanager/acme/account.key")
DEFAULT_HTTP_TIMEOUT = 30.0
ACME_POLL_INTERVAL = 3.0
ACME_MAX_POLL_ATTEMPTS = 40  # 40 * 3s = 2 minutes max


@dataclass(frozen=True)
class ACMEAccount:
    """Represents a registered ACME account."""

    account_url: str
    key_pem: bytes


@dataclass(frozen=True)
class ACMEOrder:
    """Represents an ACME certificate order."""

    order_url: str
    authorization_urls: list[str]
    finalize_url: str


@dataclass(frozen=True)
class ACMEChallenge:
    """Represents a DNS-01 ACME challenge."""

    challenge_url: str
    token: str
    key_authorization_digest: str


@dataclass(frozen=True)
class ACMECertificateResult:
    """Outcome of an ACME certificate issuance."""

    success: bool
    fullchain_pem: bytes | None
    error: str | None


def _b64url(data: bytes) -> str:
    """Base64url encode without padding."""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _jwk_thumbprint(public_key: rsa.RSAPublicKey) -> str:
    """Compute the JWK thumbprint (RFC 7638) of an RSA public key.

    Args:
        public_key: The RSA public key.

    Returns:
        The JWK thumbprint string.
    """
    pn = public_key.public_numbers()
    jwk_dict = {
        "e": _b64url(pn.e.to_bytes((pn.e.bit_length() + 7) // 8, "big")),
        "kty": "RSA",
        "n": _b64url(pn.n.to_bytes((pn.n.bit_length() + 7) // 8, "big")),
    }
    canonical_json = json.dumps(jwk_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    thumbprint_digest = hashlib.sha256(canonical_json).digest()
    return _b64url(thumbprint_digest)


def _jws_header(account_key: rsa.RSAPrivateKey, url: str, nonce: str, kid: str | None = None) -> dict[str, Any]:
    """Build a JWS protected header for ACME requests.

    Args:
        account_key: The RSA private key.
        url: The request URL.
        nonce: The ACME nonce.
        kid: Optional Key ID (account URL).

    Returns:
        The JWS protected header dictionary.
    """
    header: dict[str, Any] = {
        "alg": "RS256",
        "nonce": nonce,
        "url": url,
    }
    if kid:
        header["kid"] = kid
    else:
        pn = account_key.public_key().public_numbers()
        header["jwk"] = {
            "e": _b64url(pn.e.to_bytes((pn.e.bit_length() + 7) // 8, "big")),
            "kty": "RSA",
            "n": _b64url(pn.n.to_bytes((pn.n.bit_length() + 7) // 8, "big")),
        }
    return header


def _sign_jws(
    account_key: rsa.RSAPrivateKey, url: str, nonce: str, payload: dict[str, Any] | str, kid: str | None = None
) -> dict[str, Any]:
    """Sign an ACME JWS request body.

    Args:
        account_key: The RSA private key.
        url: The request URL.
        nonce: The ACME nonce.
        payload: The request payload.
        kid: Optional Key ID (account URL).

    Returns:
        The JWS request dictionary.
    """
    header = _jws_header(account_key, url, nonce, kid)
    protected64 = _b64url(json.dumps(header).encode("utf-8"))

    if payload == "":
        payload64 = ""
    else:
        payload_bytes = json.dumps(payload).encode("utf-8") if isinstance(payload, dict) else payload.encode("utf-8")
        payload64 = _b64url(payload_bytes)

    signature_input = f"{protected64}.{payload64}".encode("ascii")
    sig = account_key.sign(signature_input, padding.PKCS1v15(), hashes.SHA256())

    return {
        "protected": protected64,
        "payload": payload64,
        "signature": _b64url(sig),
    }


async def get_acme_directory(
    directory_url: str = DEFAULT_ACME_DIRECTORY, timeout: float = DEFAULT_HTTP_TIMEOUT
) -> dict[str, Any]:
    """Fetch the ACME directory endpoints.

    Args:
        directory_url: The URL of the ACME directory.
        timeout: HTTP timeout in seconds.

    Returns:
        The ACME directory dictionary.

    Raises:
        httpx.RequestError: On request failure.
        httpx.HTTPStatusError: On HTTP error status.
    """
    logger.info("Fetching ACME directory from %s", directory_url)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(directory_url)
        response.raise_for_status()
        return dict(response.json())


def create_or_load_account_key(key_path: Path = DEFAULT_ACCOUNT_KEY_PATH) -> rsa.RSAPrivateKey:
    """Load existing ACME account key or generate and persist a new one.

    Args:
        key_path: The path to the account key file.

    Returns:
        The RSA private key for the account.
    """
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        logger.info("Loading existing ACME account key from %s", key_path)
        key_pem = key_path.read_bytes()
        key = serialization.load_pem_private_key(key_pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("Loaded key is not an RSA private key.")
        return key

    logger.info("Generating new ACME account key at %s", key_path)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(key_pem)
    key_path.chmod(0o600)
    return private_key


async def get_nonce(new_nonce_url: str, timeout: float = DEFAULT_HTTP_TIMEOUT) -> str:
    """Get a fresh anti-replay nonce from the ACME server.

    Args:
        new_nonce_url: The URL to request a new nonce.
        timeout: HTTP timeout in seconds.

    Returns:
        The new nonce string.

    Raises:
        ValueError: If the Replay-Nonce header is missing.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.head(new_nonce_url)
        response.raise_for_status()
        nonce = response.headers.get("Replay-Nonce")
        if not nonce:
            raise ValueError("Replay-Nonce header missing from response")
        return nonce


async def register_account(
    account_key: rsa.RSAPrivateKey, new_account_url: str, new_nonce_url: str, timeout: float = DEFAULT_HTTP_TIMEOUT
) -> ACMEAccount:
    """Register or find an existing ACME account.

    Args:
        account_key: The RSA private key.
        new_account_url: The ACME newAccount endpoint.
        new_nonce_url: The ACME newNonce endpoint.
        timeout: HTTP timeout in seconds.

    Returns:
        The ACMEAccount object.
    """
    logger.info("Registering ACME account")
    nonce = await get_nonce(new_nonce_url, timeout)
    payload = {"termsOfServiceAgreed": True}
    jws = _sign_jws(account_key, new_account_url, nonce, payload)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(new_account_url, json=jws, headers={"Content-Type": "application/jose+json"})
        response.raise_for_status()

        account_url = response.headers.get("Location")
        if not account_url:
            raise ValueError("Location header missing from newAccount response")

        key_pem = account_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return ACMEAccount(account_url=account_url, key_pem=key_pem)


async def create_order(
    account: ACMEAccount,
    account_key: rsa.RSAPrivateKey,
    domain: str,
    new_order_url: str,
    new_nonce_url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> ACMEOrder:
    """Create a new certificate order for a domain.

    Args:
        account: The registered ACME account.
        account_key: The RSA private key.
        domain: The domain to order a certificate for.
        new_order_url: The ACME newOrder endpoint.
        new_nonce_url: The ACME newNonce endpoint.
        timeout: HTTP timeout in seconds.

    Returns:
        The ACMEOrder object.
    """
    logger.info("Creating order for domain %s", domain)
    nonce = await get_nonce(new_nonce_url, timeout)
    payload = {"identifiers": [{"type": "dns", "value": domain}]}
    jws = _sign_jws(account_key, new_order_url, nonce, payload, kid=account.account_url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(new_order_url, json=jws, headers={"Content-Type": "application/jose+json"})
        response.raise_for_status()

        order_url = response.headers.get("Location")
        if not order_url:
            raise ValueError("Location header missing from newOrder response")

        data = response.json()
        return ACMEOrder(
            order_url=order_url,
            authorization_urls=data.get("authorizations", []),
            finalize_url=data.get("finalize", ""),
        )


def compute_key_authorization_digest(token: str, account_key: rsa.RSAPrivateKey) -> str:
    """Compute base64url(SHA-256(token.thumbprint)) for DNS-01 TXT record.

    Args:
        token: The challenge token.
        account_key: The RSA private key.

    Returns:
        The digest string for the TXT record.
    """
    thumbprint = _jwk_thumbprint(account_key.public_key())
    key_auth = f"{token}.{thumbprint}"
    digest = hashlib.sha256(key_auth.encode("ascii")).digest()
    return _b64url(digest)


async def get_dns01_challenge(
    account: ACMEAccount,
    account_key: rsa.RSAPrivateKey,
    authorization_url: str,
    new_nonce_url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> ACMEChallenge:
    """Extract the DNS-01 challenge from an authorization.

    Args:
        account: The ACME account.
        account_key: The RSA private key.
        authorization_url: The authorization URL.
        new_nonce_url: The newNonce URL.
        timeout: HTTP timeout in seconds.

    Returns:
        The ACMEChallenge object.
    """
    logger.info("Fetching authorization from %s", authorization_url)
    nonce = await get_nonce(new_nonce_url, timeout)
    jws = _sign_jws(account_key, authorization_url, nonce, "", kid=account.account_url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(authorization_url, json=jws, headers={"Content-Type": "application/jose+json"})
        response.raise_for_status()

        data = response.json()
        challenges = data.get("challenges", [])
        for ch in challenges:
            if ch.get("type") == "dns-01":
                token = ch.get("token", "")
                digest = compute_key_authorization_digest(token, account_key)
                return ACMEChallenge(challenge_url=ch.get("url", ""), token=token, key_authorization_digest=digest)

        raise ValueError("No dns-01 challenge found in authorization")


async def respond_to_challenge(
    account: ACMEAccount,
    account_key: rsa.RSAPrivateKey,
    challenge_url: str,
    new_nonce_url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> bool:
    """Respond to an ACME challenge to trigger validation.

    Args:
        account: The ACME account.
        account_key: The RSA private key.
        challenge_url: The challenge URL.
        new_nonce_url: The newNonce URL.
        timeout: HTTP timeout in seconds.

    Returns:
        True if the request was successful.
    """
    logger.info("Responding to challenge at %s", challenge_url)
    nonce = await get_nonce(new_nonce_url, timeout)
    payload: dict[str, Any] = {}  # Empty object for challenge response
    jws = _sign_jws(account_key, challenge_url, nonce, payload, kid=account.account_url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(challenge_url, json=jws, headers={"Content-Type": "application/jose+json"})
        response.raise_for_status()
        return True


async def poll_order_status(
    account: ACMEAccount,
    account_key: rsa.RSAPrivateKey,
    order_url: str,
    new_nonce_url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> dict[str, Any]:
    """Poll the order status until ready, valid, or invalid.

    Args:
        account: The ACME account.
        account_key: The RSA private key.
        order_url: The order URL.
        new_nonce_url: The newNonce URL.
        timeout: HTTP timeout in seconds.

    Returns:
        The final order JSON dictionary.
    """
    logger.info("Polling order status for %s", order_url)
    for _ in range(ACME_MAX_POLL_ATTEMPTS):
        nonce = await get_nonce(new_nonce_url, timeout)
        jws = _sign_jws(account_key, order_url, nonce, "", kid=account.account_url)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(order_url, json=jws, headers={"Content-Type": "application/jose+json"})
            response.raise_for_status()

            data = response.json()
            status = data.get("status")
            if status in ("ready", "valid", "invalid"):
                return dict(data)

        await asyncio.sleep(ACME_POLL_INTERVAL)

    raise TimeoutError("Timed out polling order status")


async def finalize_order(
    account: ACMEAccount,
    account_key: rsa.RSAPrivateKey,
    finalize_url: str,
    csr_der: bytes,
    new_nonce_url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> str:
    """Finalize an order by submitting the CSR. Returns certificate URL.

    Args:
        account: The ACME account.
        account_key: The RSA private key.
        finalize_url: The order finalize URL.
        csr_der: The CSR in DER format.
        new_nonce_url: The newNonce URL.
        timeout: HTTP timeout in seconds.

    Returns:
        The URL to the certificate.
    """
    logger.info("Finalizing order at %s", finalize_url)
    nonce = await get_nonce(new_nonce_url, timeout)
    payload = {"csr": _b64url(csr_der)}
    jws = _sign_jws(account_key, finalize_url, nonce, payload, kid=account.account_url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(finalize_url, json=jws, headers={"Content-Type": "application/jose+json"})
        response.raise_for_status()

        data = response.json()
        status = data.get("status")

        # If processing, poll until valid
        if status == "processing":
            order_url = response.headers.get("Location", finalize_url)
            data = await poll_order_status(account, account_key, order_url, new_nonce_url, timeout)
            status = data.get("status")

        if status != "valid":
            raise ValueError(f"Order not valid after finalize: {status}")

        cert_url = data.get("certificate")
        if not cert_url:
            raise ValueError("Certificate URL missing from finalized order")

        return cert_url


async def download_certificate(
    account: ACMEAccount,
    account_key: rsa.RSAPrivateKey,
    certificate_url: str,
    new_nonce_url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> bytes:
    """Download the signed fullchain certificate PEM.

    Args:
        account: The ACME account.
        account_key: The RSA private key.
        certificate_url: The certificate URL.
        new_nonce_url: The newNonce URL.
        timeout: HTTP timeout in seconds.

    Returns:
        The fullchain certificate PEM as bytes.
    """
    logger.info("Downloading certificate from %s", certificate_url)
    nonce = await get_nonce(new_nonce_url, timeout)
    jws = _sign_jws(account_key, certificate_url, nonce, "", kid=account.account_url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            certificate_url,
            json=jws,
            headers={"Content-Type": "application/jose+json", "Accept": "application/pem-certificate-chain"},
        )
        response.raise_for_status()
        return response.content


async def perform_dns01_flow(
    domain: str,
    duckdns_token: str,
    private_key: rsa.RSAPrivateKey,
    directory_url: str = DEFAULT_ACME_DIRECTORY,
    account_key_path: Path = DEFAULT_ACCOUNT_KEY_PATH,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> ACMECertificateResult:
    """Orchestrate the complete ACME DNS-01 challenge flow.

    Steps:
    1. Fetch ACME directory
    2. Load/create account key
    3. Register account
    4. Create order for domain
    5. Get DNS-01 challenge
    6. Compute key authorization digest
    7. Set DuckDNS TXT record (import from app.engine.tls_manager)
    8. Respond to challenge
    9. Poll until authorization is valid
    10. Finalize order with CSR
    11. Download signed certificate
    12. Clear DuckDNS TXT record (in finally block)

    Args:
        domain: The domain to issue the certificate for.
        duckdns_token: The DuckDNS token for DNS-01 updates.
        private_key: The domain's private key for the CSR.
        directory_url: The ACME directory URL.
        account_key_path: Path to the ACME account key.
        timeout: HTTP timeout in seconds.

    Returns:
        An ACMECertificateResult indicating success or failure.
    """
    try:
        # 1. Fetch directory
        directory = await get_acme_directory(directory_url, timeout)
        new_nonce_url = directory.get("newNonce", "")
        new_account_url = directory.get("newAccount", "")
        new_order_url = directory.get("newOrder", "")

        # 2. Load/create account key
        account_key = create_or_load_account_key(account_key_path)

        # 3. Register account
        account = await register_account(account_key, new_account_url, new_nonce_url, timeout)

        # 4. Create order
        order = await create_order(account, account_key, domain, new_order_url, new_nonce_url, timeout)

        if not order.authorization_urls:
            raise ValueError("No authorization URLs returned from newOrder")

        authz_url = order.authorization_urls[0]

        # 5 & 6. Get challenge & compute digest
        challenge = await get_dns01_challenge(account, account_key, authz_url, new_nonce_url, timeout)

        txt_set = False
        try:
            # 7. Set TXT record
            logger.info("Setting DuckDNS TXT record for %s", domain)
            await set_duckdns_txt_record(domain, duckdns_token, challenge.key_authorization_digest)
            txt_set = True

            await asyncio.sleep(10.0)  # Allow DNS propagation

            # 8. Respond to challenge
            await respond_to_challenge(account, account_key, challenge.challenge_url, new_nonce_url, timeout)

            # 9. Poll order status (implicitly checks authz via order ready status)
            final_order_data = await poll_order_status(account, account_key, order.order_url, new_nonce_url, timeout)
            if final_order_data.get("status") == "invalid":
                raise ValueError("Order became invalid during validation")

            # 10. Finalize order with CSR
            csr = (
                x509.CertificateSigningRequestBuilder()
                .subject_name(
                    x509.Name(
                        [
                            x509.NameAttribute(NameOID.COMMON_NAME, domain),
                        ]
                    )
                )
                .sign(private_key, hashes.SHA256())
            )

            csr_der = csr.public_bytes(serialization.Encoding.DER)
            cert_url = await finalize_order(account, account_key, order.finalize_url, csr_der, new_nonce_url, timeout)

            # 11. Download signed certificate
            cert_pem = await download_certificate(account, account_key, cert_url, new_nonce_url, timeout)

            return ACMECertificateResult(success=True, fullchain_pem=cert_pem, error=None)

        finally:
            if txt_set:
                logger.info("Clearing DuckDNS TXT record for %s", domain)
                try:
                    await clear_duckdns_txt_record(domain, duckdns_token)
                except Exception as e:
                    logger.warning("Failed to clear DuckDNS TXT record: %s", e)

    except Exception as e:
        logger.error("ACME flow failed: %s", e)
        return ACMECertificateResult(success=False, fullchain_pem=None, error=str(e))


__all__ = [
    "DEFAULT_ACCOUNT_KEY_PATH",
    "DEFAULT_ACME_DIRECTORY",
    "DEFAULT_HTTP_TIMEOUT",
    "ACMEAccount",
    "ACMECertificateResult",
    "ACMEChallenge",
    "ACMEOrder",
    "compute_key_authorization_digest",
    "create_or_load_account_key",
    "create_order",
    "download_certificate",
    "finalize_order",
    "get_acme_directory",
    "get_dns01_challenge",
    "get_nonce",
    "perform_dns01_flow",
    "poll_order_status",
    "register_account",
    "respond_to_challenge",
]
