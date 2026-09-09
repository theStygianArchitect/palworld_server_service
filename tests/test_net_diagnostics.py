"""Unit tests for app.monitoring.net_diagnostics — gateway resolution and ping probe logic."""

# pylint: disable=missing-function-docstring
# Rationale: Pytest test function names are self-descriptive and documented via assertions.
# pylint: disable=protected-access
# Rationale: Unit tests intentionally inspect private tracker state to verify reset/update behavior.

from __future__ import annotations

import socket
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.monitoring.net_diagnostics import (
    NetworkThroughputTracker,
    check_udp_drops,
    resolve_default_gateway,
)

# =============================================================================
# resolve_default_gateway — pure-Python /proc/net/route reader
# =============================================================================


def test_resolve_default_gateway_non_posix() -> None:
    """Returns fallback IP when is_posix() returns False (Windows/macOS host)."""
    with patch("app.monitoring.net_diagnostics.is_posix", return_value=False):
        result = resolve_default_gateway()
    assert result == "192.168.1.1"


def test_resolve_default_gateway_no_proc_file(tmp_path: Path) -> None:
    """Returns fallback IP when /proc/net/route does not exist."""
    nonexistent = tmp_path / "nonexistent_route"

    def _fake_path(p: str) -> Path:
        return nonexistent if "proc" in str(p) else Path(p)

    with (
        patch("app.monitoring.net_diagnostics.is_posix", return_value=True),
        patch("app.monitoring.net_diagnostics.Path", side_effect=_fake_path),
    ):
        # Even if Path resolution is mocked, a missing file should fallback cleanly
        result = resolve_default_gateway()
    assert result == "192.168.1.1"


def test_resolve_default_gateway_valid_proc_entry(tmp_path: Path) -> None:
    """Parses a well-formed /proc/net/route file and extracts the default gateway correctly.

    Iface  Destination  Gateway  Flags  RefCnt  Use  Metric  Mask      MTU  Window  IRTT
    eth0   00000000     0101A8C0  0003   0       0    100     00000000  0    0       0

    Gateway 0101A8C0 in little-endian hex = 192.168.1.1 in dotted notation.
    """
    # 192.168.1.1 → bytes [192, 168, 1, 1] → little-endian 32-bit = 0x0101A8C0
    gw_ip = "192.168.1.1"
    gw_packed = struct.pack("<I", int.from_bytes(socket.inet_aton(gw_ip), "big"))
    gw_hex = gw_packed.hex().upper()

    proc_content = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        f"eth0\t00000000\t{gw_hex}\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "eth0\t0101A8C0\t00000000\t0001\t0\t0\t0\tFFFFFF00\t0\t0\t0\n"
    )

    fake_proc = tmp_path / "route"
    fake_proc.write_text(proc_content, encoding="ascii")

    with (
        patch("app.monitoring.net_diagnostics.is_posix", return_value=True),
        patch("app.monitoring.net_diagnostics.Path", return_value=fake_proc),
    ):
        result = resolve_default_gateway()

    assert result == gw_ip


def test_resolve_default_gateway_no_default_route(tmp_path: Path) -> None:
    """Returns fallback IP when no default (destination 00000000) route entry exists."""
    proc_content = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t0101A8C0\t00000000\t0001\t0\t0\t0\tFFFFFF00\t0\t0\t0\n"
    )
    fake_proc = tmp_path / "route"
    fake_proc.write_text(proc_content, encoding="ascii")

    with (
        patch("app.monitoring.net_diagnostics.is_posix", return_value=True),
        patch("app.monitoring.net_diagnostics.Path", return_value=fake_proc),
    ):
        result = resolve_default_gateway()

    assert result == "192.168.1.1"


def test_resolve_default_gateway_ioerror(tmp_path: Path) -> None:
    """Returns fallback IP when reading /proc/net/route raises OSError."""
    # Create a real file to satisfy any exist checks, but FakePathExists overrides read_text
    (tmp_path / "route_exists").write_text("header\n", encoding="ascii")

    class FakePathExists:
        """Fake Path wrapper that exists but raises OSError on read_text."""

        def __init__(self, _p: str) -> None:
            pass

        def exists(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:
            raise OSError("Simulated IO failure")

    with (
        patch("app.monitoring.net_diagnostics.is_posix", return_value=True),
        patch("app.monitoring.net_diagnostics.Path", return_value=FakePathExists("/")),
    ):
        result = resolve_default_gateway()

    assert result == "192.168.1.1"


def test_resolve_default_gateway_malformed_entries(tmp_path: Path) -> None:
    """Returns fallback IP when /proc/net/route has malformed short lines."""
    proc_content = "Iface\tDestination\neth0\t00000000\n"  # Too few columns
    fake_proc = tmp_path / "route"
    fake_proc.write_text(proc_content, encoding="ascii")

    with (
        patch("app.monitoring.net_diagnostics.is_posix", return_value=True),
        patch("app.monitoring.net_diagnostics.Path", return_value=fake_proc),
    ):
        result = resolve_default_gateway()

    assert result == "192.168.1.1"


# =============================================================================
# NetworkThroughputTracker
# =============================================================================


def test_throughput_tracker_first_call_returns_zero() -> None:
    """First throughput calculation returns zeros before any previous baseline."""
    tracker = NetworkThroughputTracker()
    counters = MagicMock()
    counters.bytes_recv = 1000000
    counters.bytes_sent = 500000
    result = tracker.calculate_rates(counters)
    assert result["rx_rate_kbps"] == 0.0
    assert result["tx_rate_kbps"] == 0.0


def test_throughput_tracker_reset_clears_state() -> None:
    """reset() zeroes all internal tracking counters."""
    tracker = NetworkThroughputTracker()
    tracker._last_net_bytes_recv = 100000
    tracker._last_net_bytes_sent = 50000
    tracker._last_net_time = 12345.0
    tracker.reset()
    assert tracker._last_net_bytes_recv == 0
    assert tracker._last_net_bytes_sent == 0
    assert tracker._last_net_time == 0.0


# =============================================================================
# check_udp_drops
# =============================================================================


def test_check_udp_drops_no_drops() -> None:
    """Returns False when all interface drop/error counters are zero."""
    mock_counters = MagicMock()
    mock_counters.dropin = 0
    mock_counters.errin = 0
    with patch("app.monitoring.net_diagnostics.psutil.net_io_counters", return_value={"eth0": mock_counters}):
        result = check_udp_drops()
    assert result is False


def test_check_udp_drops_with_drops() -> None:
    """Returns True when an interface reports incoming dropped frames."""
    mock_counters = MagicMock()
    mock_counters.dropin = 5
    mock_counters.errin = 0
    with patch("app.monitoring.net_diagnostics.psutil.net_io_counters", return_value={"eth0": mock_counters}):
        result = check_udp_drops()
    assert result is True
