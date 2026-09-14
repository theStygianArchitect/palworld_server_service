"""Tests for ACME DNS-01 client."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.engine.acme_client import (
    ACMEAccount,
    _jwk_thumbprint,
    _sign_jws,
    create_or_load_account_key,
    get_acme_directory,
    perform_dns01_flow,
)


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_jwk_thumbprint_computation(rsa_key):
    thumb = _jwk_thumbprint(rsa_key.public_key())
    assert isinstance(thumb, str)
    assert len(thumb) > 0
    # verify it's base64url encoded
    assert "+" not in thumb
    assert "/" not in thumb
    assert "=" not in thumb


def test_jws_signing(rsa_key):
    url = "https://example.com/acme/new-order"
    nonce = "test-nonce-123"
    payload = {"identifiers": [{"type": "dns", "value": "example.com"}]}

    # Sign with JWK
    jws_jwk = _sign_jws(rsa_key, url, nonce, payload)
    assert "protected" in jws_jwk
    assert "payload" in jws_jwk
    assert "signature" in jws_jwk

    # Sign with KID
    kid = "https://example.com/acme/acct/1"
    jws_kid = _sign_jws(rsa_key, url, nonce, payload, kid=kid)
    assert "protected" in jws_kid

    # Decode protected header
    import base64

    protected_json = base64.urlsafe_b64decode(jws_kid["protected"] + "===").decode("utf-8")
    header = json.loads(protected_json)
    assert header["kid"] == kid
    assert header["nonce"] == nonce
    assert header["url"] == url


@pytest.mark.asyncio
async def test_get_acme_directory():
    directory_url = "https://example.com/dir"
    directory_data = {"newNonce": "https://example.com/nonce"}

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = directory_data
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await get_acme_directory(directory_url)
        assert res == directory_data


def test_create_or_load_account_key_new(tmp_path):
    key_path = tmp_path / "account.key"
    key = create_or_load_account_key(key_path)
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key_path.exists()

    content = key_path.read_bytes()
    assert b"BEGIN RSA PRIVATE KEY" in content or b"BEGIN PRIVATE KEY" in content


def test_create_or_load_account_key_existing(tmp_path, rsa_key):
    key_path = tmp_path / "account.key"
    key_pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(key_pem)

    loaded_key = create_or_load_account_key(key_path)
    assert loaded_key.public_key().public_numbers().n == rsa_key.public_key().public_numbers().n


@pytest.mark.asyncio
@patch("app.engine.acme_client.clear_duckdns_txt_record", new_callable=AsyncMock)
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("app.engine.acme_client.download_certificate", new_callable=AsyncMock)
@patch("app.engine.acme_client.finalize_order", new_callable=AsyncMock)
@patch("app.engine.acme_client.poll_order_status", new_callable=AsyncMock)
@patch("app.engine.acme_client.respond_to_challenge", new_callable=AsyncMock)
@patch("app.engine.acme_client.set_duckdns_txt_record", new_callable=AsyncMock)
@patch("app.engine.acme_client.get_dns01_challenge", new_callable=AsyncMock)
@patch("app.engine.acme_client.create_order", new_callable=AsyncMock)
@patch("app.engine.acme_client.register_account", new_callable=AsyncMock)
@patch("app.engine.acme_client.get_acme_directory", new_callable=AsyncMock)
async def test_perform_dns01_flow_success(
    mock_dir,
    mock_reg,
    mock_order,
    mock_chal,
    mock_set_txt,
    mock_resp,
    mock_poll,
    mock_fin,
    mock_dl,
    mock_sleep,
    mock_clear_txt,
    tmp_path,
    rsa_key,
):
    key_path = tmp_path / "account.key"
    domain = "example.com"
    token = "duckdns-token"

    mock_dir.return_value = {"newNonce": "url-nonce", "newAccount": "url-acct", "newOrder": "url-order"}
    mock_reg.return_value = ACMEAccount(account_url="url-acct-1", key_pem=b"")
    mock_order.return_value = MagicMock(
        authorization_urls=["url-authz"], order_url="url-order", finalize_url="url-fin"
    )
    mock_chal.return_value = MagicMock(challenge_url="url-chal", token="tok", key_authorization_digest="dig")
    mock_poll.return_value = {"status": "ready"}
    mock_fin.return_value = "url-cert"
    mock_dl.return_value = b"cert-pem"

    res = await perform_dns01_flow(domain, token, rsa_key, account_key_path=key_path)

    assert res.success is True
    assert res.fullchain_pem == b"cert-pem"
    assert res.error is None
    mock_set_txt.assert_called_once_with(domain, token, "dig")
    mock_clear_txt.assert_called_once_with(domain, token)


@pytest.mark.asyncio
@patch("app.engine.acme_client.get_acme_directory", side_effect=Exception("API down"))
async def test_perform_dns01_flow_acme_failure(mock_dir, tmp_path, rsa_key):
    key_path = tmp_path / "account.key"
    domain = "example.com"
    token = "duckdns-token"

    res = await perform_dns01_flow(domain, token, rsa_key, account_key_path=key_path)
    assert res.success is False
    assert "API down" in res.error
    assert res.fullchain_pem is None


@pytest.mark.asyncio
@patch("app.engine.acme_client.clear_duckdns_txt_record", new_callable=AsyncMock)
@patch("app.engine.acme_client.respond_to_challenge", side_effect=Exception("Challenge failed"))
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("app.engine.acme_client.set_duckdns_txt_record", new_callable=AsyncMock)
@patch("app.engine.acme_client.get_dns01_challenge", new_callable=AsyncMock)
@patch("app.engine.acme_client.create_order", new_callable=AsyncMock)
@patch("app.engine.acme_client.register_account", new_callable=AsyncMock)
@patch("app.engine.acme_client.get_acme_directory", new_callable=AsyncMock)
async def test_perform_dns01_flow_cleans_txt_on_failure(
    mock_dir,
    mock_reg,
    mock_order,
    mock_chal,
    mock_set_txt,
    mock_sleep,
    mock_resp,
    mock_clear_txt,
    tmp_path,
    rsa_key,
):
    key_path = tmp_path / "account.key"
    domain = "example.com"
    token = "duckdns-token"

    mock_dir.return_value = {"newNonce": "url-nonce", "newAccount": "url-acct", "newOrder": "url-order"}
    mock_reg.return_value = ACMEAccount(account_url="url-acct-1", key_pem=b"")
    mock_order.return_value = MagicMock(authorization_urls=["url-authz"])
    mock_chal.return_value = MagicMock(key_authorization_digest="dig")

    res = await perform_dns01_flow(domain, token, rsa_key, account_key_path=key_path)

    assert res.success is False
    mock_set_txt.assert_called_once()
    mock_clear_txt.assert_called_once()
