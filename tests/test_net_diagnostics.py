"""Unit tests for active network diagnostics and probe ingress validation."""

from unittest.mock import MagicMock, patch

import pytest

from app.monitoring.net_diagnostics import execute_ping_probes, validate_probe_target


def test_validate_probe_target_valid_ipv4():
    """Verify that standard IPv4 addresses pass validation."""
    assert validate_probe_target("127.0.0.1") == "127.0.0.1"
    assert validate_probe_target("192.168.1.1") == "192.168.1.1"
    assert validate_probe_target("1.1.1.1") == "1.1.1.1"
    assert validate_probe_target("8.8.8.8") == "8.8.8.8"
    assert validate_probe_target(" 10.0.0.1 ") == "10.0.0.1"


def test_validate_probe_target_valid_ipv6():
    """Verify that valid IPv6 addresses pass validation."""
    assert validate_probe_target("::1") == "::1"
    assert validate_probe_target("2001:4860:4860::8888") == "2001:4860:4860::8888"


def test_validate_probe_target_valid_hostnames():
    """Verify that valid RFC 1123 hostnames and FQDNs pass validation."""
    assert validate_probe_target("localhost") == "localhost"
    assert validate_probe_target("gateway.local") == "gateway.local"
    assert validate_probe_target("thestygianarchitect.duckdns.org") == "thestygianarchitect.duckdns.org"
    assert validate_probe_target("one.one.one.one") == "one.one.one.one"


def test_validate_probe_target_rejects_command_flags():
    """Verify that CLI flags beginning with hyphens are strictly rejected."""
    with pytest.raises(ValueError, match="Probe target cannot start with a hyphen/flag"):
        validate_probe_target("-c")

    with pytest.raises(ValueError, match="Probe target cannot start with a hyphen/flag"):
        validate_probe_target("-i 0.2")

    with pytest.raises(ValueError, match="Probe target cannot start with a hyphen/flag"):
        validate_probe_target("--help")

    with pytest.raises(ValueError, match="Probe target cannot start with a hyphen/flag"):
        validate_probe_target("-f")


def test_validate_probe_target_rejects_injections_and_metacharacters():
    """Verify that command separators and shell metacharacters are rejected."""
    with pytest.raises(ValueError, match="Invalid probe target format"):
        validate_probe_target("1.1.1.1; ls")

    with pytest.raises(ValueError, match="Invalid probe target format"):
        validate_probe_target("1.1.1.1 && cat /etc/passwd")

    with pytest.raises(ValueError, match="Invalid probe target format"):
        validate_probe_target("1.1.1.1 | rm -rf")

    with pytest.raises(ValueError, match="Invalid probe target format"):
        validate_probe_target("1.1.1.1`id`")

    with pytest.raises(ValueError, match="Invalid probe target format"):
        validate_probe_target("1.1.1.1$(id)")


def test_validate_probe_target_rejects_empty_and_invalid_types():
    """Verify that empty, whitespace-only, or non-string inputs are rejected."""
    with pytest.raises(ValueError, match="Probe target must be a string"):
        validate_probe_target(None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Probe target must be a string"):
        validate_probe_target(123)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Probe target cannot be empty"):
        validate_probe_target("")

    with pytest.raises(ValueError, match="Probe target cannot be empty"):
        validate_probe_target("   ")


def test_execute_ping_probes_handles_invalid_target_gracefully():
    """Verify that execute_ping_probes safely catches invalid targets without executing ping."""
    res = execute_ping_probes("-c 10 1.1.1.1")
    assert res == (999.0, 99.0, 100.0)

    res_empty = execute_ping_probes("")
    assert res_empty == (999.0, 99.0, 100.0)


def test_execute_ping_probes_invokes_subprocess_with_validated_target():
    """Verify that subprocess receives sanitized target and bounded count."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "rtt min/avg/max/mdev = 5.0/10.0/15.0/2.5 ms, 0% packet loss"

    with patch("subprocess.run", return_value=fake_proc) as mock_sub:
        res = execute_ping_probes("1.1.1.1", count=100)
        assert res == (10.0, 2.5, 0.0)
        mock_sub.assert_called_once()
        args, _ = mock_sub.call_args
        cmd = args[0]
        # Count should be clamped to max 20
        assert "20" in cmd
        assert "1.1.1.1" in cmd
