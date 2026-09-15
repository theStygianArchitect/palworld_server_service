"""Unit tests for the supervisor auth module."""

from __future__ import annotations

import os
import socket
import struct
import unittest.mock

import pytest

from app.supervisor.auth import get_peer_credentials, validate_peer_credentials


def test_get_peer_credentials_extracts_correct_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that get_peer_credentials correctly extracts and unpacks the struct."""
    monkeypatch.setattr(os, "name", "posix")
    sock = unittest.mock.MagicMock(spec=socket.socket)
    sock.getsockopt.return_value = struct.pack("3i", 1234, 1001, 1001)

    pid, uid, gid = get_peer_credentials(sock)

    assert pid == 1234
    assert uid == 1001
    assert gid == 1001
    sock.getsockopt.assert_called_once_with(socket.SOL_SOCKET, 17, 12)


def test_validate_peer_credentials_accepts_allowed_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that validate_peer_credentials accepts the explicitly allowed UID."""
    monkeypatch.setattr(os, "name", "posix")
    sock = unittest.mock.MagicMock(spec=socket.socket)
    sock.getsockopt.return_value = struct.pack("3i", 1234, 1001, 1001)

    pid, uid, gid = validate_peer_credentials(sock, allowed_uid=1001)

    assert pid == 1234
    assert uid == 1001
    assert gid == 1001


def test_validate_peer_credentials_accepts_root_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that validate_peer_credentials accepts root (0) regardless of allowed_uid."""
    monkeypatch.setattr(os, "name", "posix")
    sock = unittest.mock.MagicMock(spec=socket.socket)
    sock.getsockopt.return_value = struct.pack("3i", 5678, 0, 0)

    pid, uid, gid = validate_peer_credentials(sock, allowed_uid=1001)

    assert pid == 5678
    assert uid == 0
    assert gid == 0


def test_validate_peer_credentials_rejects_unauthorized_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that validate_peer_credentials rejects a UID that is not allowed and not root."""
    monkeypatch.setattr(os, "name", "posix")
    sock = unittest.mock.MagicMock(spec=socket.socket)
    sock.getsockopt.return_value = struct.pack("3i", 1234, 9999, 1001)

    with pytest.raises(PermissionError, match=r"Peer UID 9999 is not authorized\. Expected UID 1001 or root \(0\)\."):
        validate_peer_credentials(sock, allowed_uid=1001)


def test_get_peer_credentials_raises_on_non_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that get_peer_credentials raises an OSError if not on posix."""
    monkeypatch.setattr(os, "name", "nt")
    sock = unittest.mock.MagicMock(spec=socket.socket)

    with pytest.raises(OSError, match="SO_PEERCRED is not available on this platform"):
        get_peer_credentials(sock)


def test_get_peer_credentials_raises_on_getsockopt_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that get_peer_credentials propagates OSError from getsockopt."""
    monkeypatch.setattr(os, "name", "posix")
    sock = unittest.mock.MagicMock(spec=socket.socket)
    sock.getsockopt.side_effect = OSError("Mock socket error")

    with pytest.raises(OSError, match="Failed to get peer credentials: Mock socket error"):
        get_peer_credentials(sock)
