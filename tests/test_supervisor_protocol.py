"""Tests for the supervisor protocol models and schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.supervisor.protocol import (
    ALLOWED_SERVICES,
    JSONRPCErrorDetail,
    JSONRPCErrorResponse,
    JSONRPCRequest,
    JSONRPCResult,
    ServiceRestartParams,
    SystemDeployParams,
    SystemRebootParams,
)


def test_valid_jsonrpc_request_parses() -> None:
    """Test that a valid JSON-RPC request parses correctly."""
    req = JSONRPCRequest(id="1", method="service.restart", params={"service_name": "palworld.service"})
    assert req.jsonrpc == "2.0"
    assert req.id == "1"
    assert req.method == "service.restart"
    assert req.params == {"service_name": "palworld.service"}


def test_jsonrpc_request_rejects_unknown_method() -> None:
    """Test that an unknown method is rejected with a validation error."""
    with pytest.raises(ValidationError):
        JSONRPCRequest(id="1", method="system.unknown_method")


def test_service_restart_params_rejects_unknown_service() -> None:
    """Test that an unknown service name is rejected."""
    with pytest.raises(ValidationError):
        ServiceRestartParams(service_name="unknown.service")


def test_service_restart_params_accepts_valid_service() -> None:
    """Test that all allowed services are accepted."""
    for service in ALLOWED_SERVICES:
        params = ServiceRestartParams(service_name=service)
        assert params.service_name == service


def test_system_deploy_params_rejects_shell_injection() -> None:
    """Test that a branch name with shell injection characters is rejected."""
    with pytest.raises(ValidationError):
        SystemDeployParams(target_branch="main; rm -rf /")


def test_system_deploy_params_accepts_valid_branches() -> None:
    """Test that valid branch names are accepted."""
    valid_branches = ["main", "feat/my-feature", "docs/root-supervisor-spec-62"]
    for branch in valid_branches:
        params = SystemDeployParams(target_branch=branch)
        assert params.target_branch == branch


def test_jsonrpc_result_roundtrip() -> None:
    """Test that a JSON-RPC result can be serialized and deserialized."""
    orig = JSONRPCResult(id="123", result={"status": "success"})
    dumped = orig.model_dump_json()
    loaded = JSONRPCResult.model_validate_json(dumped)
    assert orig == loaded
    assert orig.id == loaded.id
    assert orig.result == loaded.result


def test_jsonrpc_error_response_structure() -> None:
    """Test the structure of a JSON-RPC error response."""
    detail = JSONRPCErrorDetail(code=-32600, message="Invalid Request", data={"details": "bad format"})
    resp = JSONRPCErrorResponse(id="123", error=detail)
    assert resp.jsonrpc == "2.0"
    assert resp.id == "123"
    assert resp.error.code == -32600
    assert resp.error.message == "Invalid Request"
    assert resp.error.data == {"details": "bad format"}


def test_system_reboot_params_bounds() -> None:
    """Test bounds on delay_seconds for system reboot."""
    with pytest.raises(ValidationError):
        SystemRebootParams(delay_seconds=-1)

    with pytest.raises(ValidationError):
        SystemRebootParams(delay_seconds=3601)

    params = SystemRebootParams(delay_seconds=0)
    assert params.delay_seconds == 0

    params2 = SystemRebootParams(delay_seconds=3600)
    assert params2.delay_seconds == 3600


def test_service_restart_timeout_bounds() -> None:
    """Test bounds on timeout_seconds for service restart."""
    with pytest.raises(ValidationError):
        ServiceRestartParams(service_name="palworld.service", timeout_seconds=4)

    with pytest.raises(ValidationError):
        ServiceRestartParams(service_name="palworld.service", timeout_seconds=121)

    params = ServiceRestartParams(service_name="palworld.service", timeout_seconds=5)
    assert params.timeout_seconds == 5

    params2 = ServiceRestartParams(service_name="palworld.service", timeout_seconds=120)
    assert params2.timeout_seconds == 120
