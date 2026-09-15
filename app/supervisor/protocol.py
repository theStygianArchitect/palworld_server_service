"""JSON-RPC 2.0 protocol models, method whitelists, and validation schemas."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Immutable whitelists
ALLOWED_SERVICES: frozenset[str] = frozenset({
    "palworld.service",
    "palworld-manager.service",
    "palworld-dedicated.service",
})

ALLOWED_METHODS: frozenset[str] = frozenset({
    "service.restart",
    "service.status",
    "service.reload",
    "system.reboot",
    "system.deploy",
})

BRANCH_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]{0,127}$")


class ServiceRestartParams(BaseModel):
    """Parameters for restarting a systemd service."""
    service_name: str
    timeout_seconds: int = Field(default=30, ge=5, le=120)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        """Validate that the service name is permitted."""
        if v not in ALLOWED_SERVICES:
            raise ValueError(f"Service '{v}' is not permitted by supervisor policy.")
        return v


class ServiceStatusParams(BaseModel):
    """Parameters for querying systemd service status."""
    service_name: str

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        """Validate that the service name is permitted."""
        if v not in ALLOWED_SERVICES:
            raise ValueError(f"Service '{v}' is not permitted by supervisor policy.")
        return v


class ServiceReloadParams(BaseModel):
    """Parameters for reloading systemd daemon or service."""
    daemon_reload: bool = True


class SystemRebootParams(BaseModel):
    """Parameters for scheduling a host reboot."""
    delay_seconds: int = Field(default=0, ge=0, le=3600)
    reason: str = Field(default="Operator-initiated reboot via web management plane", max_length=256)


class SystemDeployParams(BaseModel):
    """Parameters for executing an update deployment."""
    target_branch: str = Field(default="main", max_length=128)

    @field_validator("target_branch")
    @classmethod
    def validate_branch_name(cls, v: str) -> str:
        """Validate that the branch name does not contain illegal characters."""
        if not BRANCH_NAME_PATTERN.match(v):
            raise ValueError(f"Branch name '{v}' contains illegal characters.")
        return v


class JSONRPCRequest(BaseModel):
    """JSON-RPC 2.0 request envelope."""
    jsonrpc: Literal["2.0"] = "2.0"
    id: str = Field(max_length=64)
    method: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        """Validate that the method is supported by the supervisor."""
        if v not in ALLOWED_METHODS:
            raise ValueError(f"Method '{v}' is not supported by supervisor.")
        return v


class JSONRPCResult(BaseModel):
    """JSON-RPC 2.0 successful result envelope."""
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: dict[str, Any]


class JSONRPCErrorDetail(BaseModel):
    """JSON-RPC 2.0 structured error detail."""
    code: int
    message: str
    data: dict[str, Any] | None = None


class JSONRPCErrorResponse(BaseModel):
    """JSON-RPC 2.0 error response envelope."""
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | None
    error: JSONRPCErrorDetail


class SupervisorClientError(Exception):
    """Raised when client transport fails (socket missing, connection refused, timeout)."""


class SupervisorRPCError(Exception):
    """Raised when supervisor responds with an application-level JSON-RPC error."""
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(f"Supervisor error {code}: {message}")
        self.code = code
        self.data = data
