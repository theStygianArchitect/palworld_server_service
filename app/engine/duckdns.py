"""DuckDNS API helpers for dynamic DNS and ACME DNS-01 challenge management.

Provides domain normalization, subdomain extraction, TXT record management,
and IP address synchronization via the DuckDNS HTTP API.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_HTTP_TIMEOUT = 15.0


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


__all__ = [
    "DEFAULT_HTTP_TIMEOUT",
    "clean_domain_name",
    "clear_duckdns_txt_record",
    "extract_subdomain",
    "set_duckdns_txt_record",
    "sync_duckdns_ip",
]
