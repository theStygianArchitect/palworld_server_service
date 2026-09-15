"""Async IPC client for the palworld-supervisor daemon."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from typing import Any

from app.supervisor.protocol import (
    JSONRPCErrorResponse,
    JSONRPCRequest,
    JSONRPCResult,
    SupervisorClientError,
    SupervisorRPCError,
)

log = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH: str = "/run/palmanager/supervisor.sock"
DEFAULT_TIMEOUT: float = 15.0


class SupervisorClient:
    """Async client for communicating with the palworld-supervisor daemon over UDS."""

    def __init__(
        self,
        socket_path: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the SupervisorClient.

        Args:
            socket_path: Optional path to the Unix socket. Defaults to env var or default path.
            timeout: Timeout in seconds for connection and requests.
        """
        self.socket_path = socket_path or os.environ.get("SUPERVISOR_SOCKET_PATH", DEFAULT_SOCKET_PATH)
        self.timeout = timeout

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return the result.

        Args:
            method: The RPC method name.
            params: Optional parameters dict.

        Returns:
            The result dict from the JSON-RPC response.

        Raises:
            SupervisorClientError: On transport failures (socket missing, timeout, connection refused).
            SupervisorRPCError: On application-level JSON-RPC errors from the supervisor.
        """
        request_id = uuid.uuid4().hex[:12]
        request = JSONRPCRequest(id=request_id, method=method, params=params or {})
        payload = request.model_dump_json().encode() + b"\n"

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise SupervisorClientError(
                f"Supervisor socket not found at {self.socket_path}. Is palworld-supervisor running?"
            ) from exc
        except ConnectionRefusedError as exc:
            raise SupervisorClientError(
                f"Connection refused at {self.socket_path}. Is palworld-supervisor running?"
            ) from exc
        except asyncio.TimeoutError as exc:
            raise SupervisorClientError(
                f"Timeout connecting to supervisor at {self.socket_path} after {self.timeout}s"
            ) from exc
        except OSError as exc:
            raise SupervisorClientError(f"OS error connecting to supervisor: {exc}") from exc

        try:
            writer.write(payload)
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            if not response_line:
                raise SupervisorClientError("Supervisor closed connection without responding")

            response_data = json.loads(response_line)

            if "error" in response_data:
                error_resp = JSONRPCErrorResponse.model_validate(response_data)
                raise SupervisorRPCError(
                    code=error_resp.error.code,
                    message=error_resp.error.message,
                    data=error_resp.error.data,
                )

            result_resp = JSONRPCResult.model_validate(response_data)
            return result_resp.result

        except asyncio.TimeoutError as exc:
            raise SupervisorClientError(
                f"Timeout waiting for supervisor response after {self.timeout}s"
            ) from exc
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def restart_service(self, service_name: str, timeout_seconds: int = 30) -> dict[str, Any]:
        """Restart a systemd service via supervisor.

        Args:
            service_name: Name of the service to restart.
            timeout_seconds: Timeout for the restart operation.

        Returns:
            The result dictionary.
        """
        return await self._send_request(
            "service.restart",
            {"service_name": service_name, "timeout_seconds": timeout_seconds},
        )

    async def get_service_status(self, service_name: str) -> dict[str, Any]:
        """Query the active state of a systemd service.

        Args:
            service_name: Name of the service.

        Returns:
            The service status dictionary.
        """
        return await self._send_request("service.status", {"service_name": service_name})

    async def reload_systemd(self, daemon_reload: bool = True) -> dict[str, Any]:
        """Reload systemd daemon configuration.

        Args:
            daemon_reload: True to perform a daemon reload.

        Returns:
            The result dictionary.
        """
        return await self._send_request("service.reload", {"daemon_reload": daemon_reload})

    async def schedule_reboot(self, delay_seconds: int = 0, reason: str = "") -> dict[str, Any]:
        """Schedule a system reboot.

        Args:
            delay_seconds: Seconds to delay the reboot.
            reason: Optional reason string.

        Returns:
            The result dictionary.
        """
        params: dict[str, Any] = {"delay_seconds": delay_seconds}
        if reason:
            params["reason"] = reason
        return await self._send_request("system.reboot", params)

    async def trigger_deploy(self, target_branch: str = "main") -> dict[str, Any]:
        """Trigger a deployment of the specified branch.

        Args:
            target_branch: Branch to deploy.

        Returns:
            The result dictionary.
        """
        return await self._send_request("system.deploy", {"target_branch": target_branch})
