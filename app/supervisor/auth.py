"""Unix Domain Socket peer credential validation via Linux kernel SO_PEERCRED."""

from __future__ import annotations

import logging
import os
import socket
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Linux kernel SO_PEERCRED constant (not always exposed by Python's socket module)
_SO_PEERCRED: int = 17
_PEERCRED_STRUCT_FORMAT: str = "3i"  # pid_t, uid_t, gid_t — each 32-bit signed int
_PEERCRED_STRUCT_SIZE: int = struct.calcsize(_PEERCRED_STRUCT_FORMAT)


def get_peer_credentials(sock: socket.socket) -> tuple[int, int, int]:
    """Get the peer credentials from a Unix domain socket.

    Args:
        sock: The socket to query.

    Returns:
        A tuple of (pid, uid, gid).

    Raises:
        OSError: If SO_PEERCRED is not available or the socket operation fails.
    """
    if os.name != "posix":
        raise OSError("SO_PEERCRED is not available on this platform")

    try:
        cred_bytes = sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, _PEERCRED_STRUCT_SIZE)
    except OSError as e:
        raise OSError(f"Failed to get peer credentials: {e}") from e

    pid, uid, gid = struct.unpack(_PEERCRED_STRUCT_FORMAT, cred_bytes)
    return pid, uid, gid


def validate_peer_credentials(sock: socket.socket, allowed_uid: int) -> tuple[int, int, int]:
    """Validate that the peer on the other end of the socket is authorized.

    Args:
        sock: The socket to check.
        allowed_uid: The UID that is allowed to connect (root is always allowed).

    Returns:
        A tuple of (pid, uid, gid) for the validated peer.

    Raises:
        PermissionError: If the peer UID is neither the allowed UID nor root (0).
        OSError: If SO_PEERCRED is not available or the socket operation fails.
    """
    pid, uid, gid = get_peer_credentials(sock)

    if uid not in (allowed_uid, 0):
        log.warning("Rejected connection from peer pid=%d uid=%d gid=%d", pid, uid, gid)
        raise PermissionError(f"Peer UID {uid} is not authorized. Expected UID {allowed_uid} or root (0).")

    log.info("Accepted connection from peer pid=%d uid=%d gid=%d", pid, uid, gid)
    return pid, uid, gid
