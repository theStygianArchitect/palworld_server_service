"""Async Unix Domain Socket JSON-RPC 2.0 server for privileged host operations."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from pydantic import BaseModel, ValidationError

from app.supervisor.auth import validate_peer_credentials
from app.supervisor.handlers import METHOD_DISPATCH
from app.supervisor.protocol import (
    JSONRPCErrorDetail,
    JSONRPCErrorResponse,
    JSONRPCRequest,
    JSONRPCResult,
    ServiceReloadParams,
    ServiceRestartParams,
    ServiceStatusParams,
    SystemDeployParams,
    SystemRebootParams,
)

log = logging.getLogger(__name__)

PARAM_MODELS: dict[str, type[BaseModel]] = {
    "service.restart": ServiceRestartParams,
    "service.status": ServiceStatusParams,
    "service.reload": ServiceReloadParams,
    "system.reboot": SystemRebootParams,
    "system.deploy": SystemDeployParams,
}


class SupervisorServer:
    """Async Unix Domain Socket JSON-RPC 2.0 server for privileged host operations."""

    def __init__(self, socket_path: str, allowed_uid: int) -> None:
        self.socket_path = socket_path
        self.allowed_uid = allowed_uid
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Bind to the UDS path, set permissions, and start accepting connections."""
        if os.name != "posix":
            raise NotImplementedError("Unix Domain Sockets are only supported on POSIX systems")

        with contextlib.suppress(FileNotFoundError):
            os.remove(self.socket_path)

        self.server = await asyncio.start_unix_server(self._handle_client, path=self.socket_path)
        os.chmod(self.socket_path, 0o660)

        if os.geteuid() == 0:  # pylint: disable=no-member  # POSIX-only; guarded by os.name check above
            try:
                import pwd  # pylint: disable=import-outside-toplevel  # POSIX-only; unavailable on Windows
                pw = pwd.getpwuid(self.allowed_uid)
                os.chown(self.socket_path, 0, pw.pw_gid)  # pylint: disable=no-member  # POSIX-only
            except (KeyError, ImportError):
                os.chown(self.socket_path, 0, self.allowed_uid)  # pylint: disable=no-member  # POSIX-only

        log.info("SupervisorServer started on %s", self.socket_path)

    async def stop(self) -> None:
        """Gracefully close the server and unlink the socket."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        with contextlib.suppress(FileNotFoundError):
            os.remove(self.socket_path)

        log.info("SupervisorServer stopped and socket %s removed", self.socket_path)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a single client connection with NDJSON line protocol."""
        sock = writer.get_extra_info("socket")

        try:
            validate_peer_credentials(sock, self.allowed_uid)
        except PermissionError:
            err = JSONRPCErrorResponse(
                id=None,
                error=JSONRPCErrorDetail(code=-32000, message="Permission denied")
            )
            writer.write(err.model_dump_json().encode() + b"\n")
            await writer.drain()
            writer.close()
            return
        except OSError:
            pass  # Non-POSIX or fallback for dev/test environments

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                if len(line) > 65536:
                    err = JSONRPCErrorResponse(
                        id=None,
                        error=JSONRPCErrorDetail(code=-32600, message="Invalid Request: line too long")
                    )
                    writer.write(err.model_dump_json().encode() + b"\n")
                    await writer.drain()
                    continue

                try:
                    request = JSONRPCRequest.model_validate_json(line)
                except ValidationError:
                    err = JSONRPCErrorResponse(
                        id=None,
                        error=JSONRPCErrorDetail(code=-32600, message="Invalid Request")
                    )
                    writer.write(err.model_dump_json().encode() + b"\n")
                    await writer.drain()
                    continue

                try:
                    param_model = PARAM_MODELS[request.method]
                    validated_params = param_model(**request.params)
                    result = await METHOD_DISPATCH[request.method](validated_params)

                    response = JSONRPCResult(id=request.id, result=result)
                    writer.write(response.model_dump_json().encode() + b"\n")
                    await writer.drain()
                except Exception:  # pylint: disable=broad-exception-caught  # JSON-RPC requires catch-all for -32603
                    log.exception("Error during dispatch")
                    err = JSONRPCErrorResponse(
                        id=request.id,
                        error=JSONRPCErrorDetail(code=-32603, message="Internal Error")
                    )
                    writer.write(err.model_dump_json().encode() + b"\n")
                    await writer.drain()

        finally:
            writer.close()
