"""Tests for the async supervisor client."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.supervisor.client import SupervisorClient
from app.supervisor.protocol import SupervisorClientError, SupervisorRPCError

_PATCH_TARGET = "app.supervisor.client.asyncio.open_unix_connection"


def _make_mock_connection(response_data: dict[str, Any] | None) -> tuple[asyncio.StreamReader, MagicMock]:
    """Create mock reader/writer for testing.

    Args:
        response_data: Data to be JSON serialized as response, or None to send EOF immediately.

    Returns:
        A tuple of (reader, writer).
    """
    reader = asyncio.StreamReader()
    if response_data is not None:
        reader.feed_data(json.dumps(response_data).encode() + b"\n")
    reader.feed_eof()

    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    writer.write = MagicMock()

    return reader, writer


@pytest.mark.asyncio
async def test_client_restart_service_success() -> None:
    """Test restarting a service successfully."""
    client = SupervisorClient(socket_path="dummy.sock")
    resp = {"jsonrpc": "2.0", "id": "test", "result": {"status": "success"}}

    with patch(_PATCH_TARGET, new_callable=AsyncMock, create=True) as mock_connect:
        mock_connect.return_value = _make_mock_connection(resp)
        result = await client.restart_service("palworld.service")

        assert result == {"status": "success"}


@pytest.mark.asyncio
async def test_client_get_service_status() -> None:
    """Test getting service status."""
    client = SupervisorClient(socket_path="dummy.sock")
    resp = {"jsonrpc": "2.0", "id": "test", "result": {"service": "palworld.service", "active_state": "active"}}

    with patch(_PATCH_TARGET, new_callable=AsyncMock, create=True) as mock_connect:
        mock_connect.return_value = _make_mock_connection(resp)
        result = await client.get_service_status("palworld.service")

        assert result == {"service": "palworld.service", "active_state": "active"}


@pytest.mark.asyncio
async def test_client_reload_systemd() -> None:
    """Test reloading systemd."""
    client = SupervisorClient(socket_path="dummy.sock")
    resp = {"jsonrpc": "2.0", "id": "test", "result": {"status": "success"}}

    with patch(_PATCH_TARGET, new_callable=AsyncMock, create=True) as mock_connect:
        mock_connect.return_value = _make_mock_connection(resp)
        result = await client.reload_systemd()

        assert result == {"status": "success"}


@pytest.mark.asyncio
async def test_client_schedule_reboot() -> None:
    """Test scheduling a reboot."""
    client = SupervisorClient(socket_path="dummy.sock")
    resp = {"jsonrpc": "2.0", "id": "test", "result": {"status": "success"}}

    with patch(_PATCH_TARGET, new_callable=AsyncMock, create=True) as mock_connect:
        mock_connect.return_value = _make_mock_connection(resp)
        result = await client.schedule_reboot(delay_seconds=60)

        assert result == {"status": "success"}


@pytest.mark.asyncio
async def test_client_trigger_deploy() -> None:
    """Test triggering a deploy."""
    client = SupervisorClient(socket_path="dummy.sock")
    resp = {"jsonrpc": "2.0", "id": "test", "result": {"status": "success"}}

    with patch(_PATCH_TARGET, new_callable=AsyncMock, create=True) as mock_connect:
        mock_connect.return_value = _make_mock_connection(resp)
        result = await client.trigger_deploy("feat/my-branch")

        assert result == {"status": "success"}


@pytest.mark.asyncio
async def test_client_handles_rpc_error() -> None:
    """Test handling of JSON-RPC errors."""
    client = SupervisorClient(socket_path="dummy.sock")
    resp = {"jsonrpc": "2.0", "id": "test", "error": {"code": -32603, "message": "Internal Error"}}

    with patch(_PATCH_TARGET, new_callable=AsyncMock, create=True) as mock_connect:
        mock_connect.return_value = _make_mock_connection(resp)

        with pytest.raises(SupervisorRPCError) as exc_info:
            await client.restart_service("palworld.service")

        assert exc_info.value.code == -32603
        assert "Internal Error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_client_handles_connection_refused() -> None:
    """Test handling of connection refused error."""
    client = SupervisorClient(socket_path="dummy.sock")

    with (
        patch(_PATCH_TARGET, side_effect=ConnectionRefusedError, create=True),
        pytest.raises(SupervisorClientError, match="Connection refused at"),
    ):
        await client.restart_service("palworld.service")


@pytest.mark.asyncio
async def test_client_handles_socket_not_found() -> None:
    """Test handling of socket not found error."""
    client = SupervisorClient(socket_path="dummy.sock")

    with (
        patch(_PATCH_TARGET, side_effect=FileNotFoundError, create=True),
        pytest.raises(SupervisorClientError, match="Supervisor socket not found"),
    ):
        await client.restart_service("palworld.service")


@pytest.mark.asyncio
async def test_client_handles_timeout() -> None:
    """Test handling of connection timeout."""
    client = SupervisorClient(socket_path="dummy.sock")

    with (
        patch(_PATCH_TARGET, side_effect=asyncio.TimeoutError, create=True),
        pytest.raises(SupervisorClientError, match="Timeout connecting to supervisor"),
    ):
        await client.restart_service("palworld.service")


@pytest.mark.asyncio
async def test_client_handles_empty_response() -> None:
    """Test handling of an empty response (EOF)."""
    client = SupervisorClient(socket_path="dummy.sock")

    with patch(_PATCH_TARGET, new_callable=AsyncMock, create=True) as mock_connect:
        mock_connect.return_value = _make_mock_connection(None)

        with pytest.raises(SupervisorClientError, match="Supervisor closed connection without responding"):
            await client.restart_service("palworld.service")
