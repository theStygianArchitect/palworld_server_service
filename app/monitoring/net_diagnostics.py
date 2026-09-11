"""Active network throughput analysis, gateway ping sweeps, and rubberbanding diagnostics.

Calculates real-time interface throughput rates, executes ICMP/TCP ping sweeps,
and performs 5-probe heuristic root-cause analysis for player rubberbanding.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import struct
import subprocess  # nosec B404 - required for ICMP ping execution (setuid binary; raw ICMP requires CAP_NET_RAW)
import time
from pathlib import Path
from typing import Any

import psutil

from app.core.config import is_posix
from app.core.logger import log
from app.core.types import NetworkDiagnosticsResult


class NetworkThroughputTracker:
    """Tracks byte deltas over time intervals to compute inbound and outbound network rates."""

    def __init__(self) -> None:
        """Initializes throughput tracking counters."""
        self._last_net_bytes_recv: int = 0
        self._last_net_bytes_sent: int = 0
        self._last_net_time: float = 0.0
        self._cached_rx_kbps: float = 0.0
        self._cached_tx_kbps: float = 0.0

    def calculate_rates(self, net_counters: Any) -> dict[str, Any]:
        """Calculates real-time transfer rates in kbps and mbps from psutil counters.

        Args:
            net_counters: psutil network I/O counter namedtuple.

        Returns:
            dict[str, Any]: Calculated rx/tx rates in kbps/mbps and bandwidth status label.
        """
        now = time.time()
        dt = now - self._last_net_time
        bytes_recv = getattr(net_counters, "bytes_recv", 0)
        bytes_sent = getattr(net_counters, "bytes_sent", 0)

        if dt >= 0.8 and self._last_net_bytes_recv > 0:
            rx_delta = max(0, bytes_recv - self._last_net_bytes_recv)
            tx_delta = max(0, bytes_sent - self._last_net_bytes_sent)
            self._cached_rx_kbps = round(rx_delta / (1024 * dt), 2)
            self._cached_tx_kbps = round(tx_delta / (1024 * dt), 2)
            self._last_net_bytes_recv = bytes_recv
            self._last_net_bytes_sent = bytes_sent
            self._last_net_time = now
        elif self._last_net_bytes_recv == 0:
            self._last_net_bytes_recv = bytes_recv
            self._last_net_bytes_sent = bytes_sent
            self._last_net_time = now

        rx_rate_kbps = self._cached_rx_kbps
        tx_rate_kbps = self._cached_tx_kbps
        rx_rate_mbps = round((rx_rate_kbps * 8) / 1000, 3)
        tx_rate_mbps = round((tx_rate_kbps * 8) / 1000, 3)

        combined_mbps = rx_rate_mbps + tx_rate_mbps
        if combined_mbps >= 50.0:
            traffic_status = "SATURATED"
        elif combined_mbps >= 15.0:
            traffic_status = "ELEVATED"
        else:
            traffic_status = "HEALTHY"

        return {
            "rx_rate_kbps": rx_rate_kbps,
            "tx_rate_kbps": tx_rate_kbps,
            "rx_rate_mbps": rx_rate_mbps,
            "tx_rate_mbps": tx_rate_mbps,
            "status": traffic_status,
        }

    def reset(self) -> None:
        """Resets throughput counters and cached rates to zero."""
        self._last_net_bytes_recv = 0
        self._last_net_bytes_sent = 0
        self._last_net_time = 0.0
        self._cached_rx_kbps = 0.0
        self._cached_tx_kbps = 0.0


def execute_ping_probes(target: str, count: int = 5) -> tuple[float, float, float]:
    """Executes ICMP ping probes returning (avg_rtt_ms, jitter_ms, packet_loss_pct).

    Falls back to TCP socket latency if ICMP ping execution is restricted.

    Args:
        target (str): Target IP or hostname to ping.
        count (int): Number of probe samples to send.

    Returns:
        tuple[float, float, float]: (avg_ms, jitter_ms, loss_pct).
    """
    if is_posix():
        ping_bin = shutil.which("ping") or "/bin/ping"
        cmd = [ping_bin, "-c", str(count), "-W", "1", target]
    else:
        cmd = ["ping", "-n", str(count), "-w", "1000", target]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)  # nosec B603
        output = proc.stdout
        loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet\s*)?loss", output, re.IGNORECASE)
        loss_pct = float(loss_match.group(1)) if loss_match else 0.0

        rtt_match = re.search(
            r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)",
            output,
            re.IGNORECASE,
        )
        if rtt_match:
            avg_ms = float(rtt_match.group(2))
            jitter_ms = float(rtt_match.group(4))
            return (round(avg_ms, 2), round(jitter_ms, 2), round(loss_pct, 1))

        win_match = re.search(
            r"Minimum\s*=\s*(\d+)ms,\s*Maximum\s*=\s*(\d+)ms,\s*Average\s*=\s*(\d+)ms",
            output,
            re.IGNORECASE,
        )
        if win_match:
            min_ms = float(win_match.group(1))
            max_ms = float(win_match.group(2))
            avg_ms = float(win_match.group(3))
            jitter_ms = max(0.0, max_ms - min_ms)
            return (round(avg_ms, 2), round(jitter_ms, 2), round(loss_pct, 1))
    except subprocess.TimeoutExpired:
        log.warning("Ping probe timed out against %s", target)
        return (999.0, 99.0, 100.0)
    except OSError as err:
        log.debug("ICMP ping execution failed against %s: %s", target, err)

    return _execute_socket_probes(target, count)


def _execute_socket_probes(target: str, count: int) -> tuple[float, float, float]:
    """Fallback TCP latency probe when raw ICMP packets cannot be transmitted."""
    latencies: list[float] = []
    dropped = 0
    port = 53 if target in {"1.1.1.1", "8.8.8.8"} else 80

    for _ in range(count):
        t0 = time.perf_counter()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect((target, port))
            latencies.append((time.perf_counter() - t0) * 1000.0)
        except OSError as err:
            log.debug("Socket latency probe dropped for %s: %s", target, err)
            dropped += 1
        finally:
            sock.close()
        time.sleep(0.05)

    if not latencies:
        return (999.0, 99.0, 100.0)

    avg_ms = sum(latencies) / len(latencies)
    jitter_ms = max(latencies) - min(latencies)
    loss_pct = (dropped / count) * 100.0
    return (round(avg_ms, 2), round(jitter_ms, 2), round(loss_pct, 1))


def resolve_default_gateway() -> str:
    """Resolves local LAN default gateway IP address via pure-Python kernel routing table inspection.

    Reads /proc/net/route directly to extract the default gateway without spawning a subprocess.
    Falls back to the conventional 192.168.1.1 address if the routing table is unavailable.

    Returns:
        str: Resolved default gateway IP address.
    """
    fallback_ip = "192.168.1.1"
    if not is_posix():
        return fallback_ip

    proc_route = Path("/proc/net/route")
    if not proc_route.exists():
        return fallback_ip

    try:
        lines = proc_route.read_text(encoding="ascii").splitlines()
        for line in lines[1:]:  # Skip header row
            parts = line.split()
            if len(parts) < 3:
                continue
            destination_hex = parts[1]
            gateway_hex = parts[2]
            # Default route: destination == 00000000
            if destination_hex == "00000000" and gateway_hex != "00000000":
                # Gateway is stored as little-endian hex; unpack and convert to dotted notation
                gateway_packed = int(gateway_hex, 16)
                gateway_bytes = struct.pack("<I", gateway_packed)
                return socket.inet_ntoa(gateway_bytes)
    except OSError as err:
        log.debug("Could not resolve default gateway via /proc/net/route (OS): %s", err)
    except ValueError as err:
        log.debug("Could not resolve default gateway via /proc/net/route (parse): %s", err)

    return fallback_ip


def check_udp_drops() -> bool:
    """Inspects Linux kernel network interface statistics for dropped or errored UDP frames."""
    try:
        net_all = psutil.net_io_counters(pernic=True)
        net_eth = net_all.get("eth0", psutil.net_io_counters())
        if getattr(net_eth, "dropin", 0) > 0 or getattr(net_eth, "errin", 0) > 0:
            return True
    except OSError as err:
        log.debug("Error checking UDP socket counters: %s", err)
    return False


def evaluate_diagnostics_verdict(metrics: dict[str, Any]) -> tuple[str, str, str, str]:
    """Heuristically determines the root cause of network rubberbanding from metrics dict."""
    server_fps = float(metrics.get("server_fps", 60.0))
    server_frame_time_ms = float(metrics.get("server_frame_time_ms", 16.6))
    gw_jitter = float(metrics.get("gw_jitter", 0.0))
    gw_loss = float(metrics.get("gw_loss", 0.0))
    net_jitter = float(metrics.get("net_jitter", 0.0))
    net_loss = float(metrics.get("net_loss", 0.0))
    udp_drops = bool(metrics.get("udp_drops", False))
    gw_avg = float(metrics.get("gw_avg", 0.0))
    net_avg = float(metrics.get("net_avg", 0.0))
    nat_aligned = bool(metrics.get("nat_aligned", False))

    if server_fps < 25.0 or server_frame_time_ms > 40.0:
        return (
            "SERVER_TICK_STARVATION",
            "⚠️ Server Engine Tick Lag (Low FPS)",
            f"The server is ticking at {server_fps:.1f} FPS ({server_frame_time_ms:.1f}ms frame time). "
            "Palworld movement reconciliation assumes >= 30 FPS. When engine ticks drop below this threshold, "
            "player positions desynchronize and snap back (rubberbanding), even with zero network latency.",
            "Lower Pal/Item spawn multipliers (PalSpawnNumRate, DropItemMaxNum), restart the server to clear "
            "memory fragmentation, or allocate more CPU threads.",
        )
    if gw_jitter > 20.0 or gw_loss > 0.0:
        return (
            "NETWORK_JITTER",
            "⚠️ Local LAN / Router Jitter Detected",
            f"Local router gateway latency variance is high (Jitter: {gw_jitter:.1f}ms, Loss: {gw_loss:.1f}%). "
            "This indicates local Wi-Fi interference, powerline ethernet instability, or router CPU saturation.",
            "Connect the server directly via Cat6 Ethernet cable, bypass Wi-Fi repeaters, and check router CPU load.",
        )
    if net_loss > 2.0 or udp_drops:
        return (
            "NAT_PACKET_LOSS",
            "⚠️ UDP Packet Drops / NAT State Saturation",
            f"Detected {net_loss:.1f}% public internet packet loss or kernel UDP drops. Router NAT state tables "
            "may be overflowing or UDP socket queues are dropping packets.",
            "Verify router port forwarding for UDP 8211. Increase Linux UDP socket buffer limits "
            "(sysctl -w net.core.rmem_max=26214400).",
        )
    if net_jitter > 35.0:
        return (
            "NETWORK_JITTER",
            "⚠️ ISP WAN Bufferbloat / Jitter",
            f"Local LAN is clean, but public internet ping spikes significantly (Jitter: {net_jitter:.1f}ms). "
            "Bufferbloat or ISP WAN congestion is causing erratic packet arrival times.",
            "Enable SQM / Smart Queue Management on your router to eliminate WAN bufferbloat under load.",
        )

    return (
        "CLEAN",
        "🟢 Network & NAT Healthy (No Bottlenecks Detected)",
        f"Local gateway latency is steady ({gw_avg:.1f}ms, jitter: {gw_jitter:.1f}ms, 0% loss). "
        f"Internet latency is steady ({net_avg:.1f}ms, jitter: {net_jitter:.1f}ms). "
        f"Server tickrate is optimal ({server_fps:.1f} FPS, {server_frame_time_ms:.1f}ms). "
        f"NAT alignment is {'VERIFIED' if nat_aligned else 'PENDING'}.",
        "Host network, NAT, and engine tick rate are running smoothly. If individual tamers report "
        "rubberbanding, the bottleneck is on their local ISP, high-latency Wi-Fi, or remote connection.",
    )


async def run_network_diagnostics_sweep(
    server_fps: float = 60.0,
    server_frame_time_ms: float = 16.6,
    nat_aligned: bool = False,
    ping_runner: Any = None,
) -> NetworkDiagnosticsResult:
    """Asynchronously runs full 5-probe network diagnostics suite.

    Args:
        server_fps (float): Current game engine frame rate.
        server_frame_time_ms (float): Current game engine frame time in milliseconds.
        nat_aligned (bool): Whether public WAN IP matches DNS record.
        ping_runner (Any): Optional custom ping probe executor function.

    Returns:
        NetworkDiagnosticsResult: Full diagnostic telemetry, verdict code, and fix recommendations.
    """
    gateway_ip = resolve_default_gateway()
    runner = ping_runner or execute_ping_probes
    gw_stats = await asyncio.to_thread(runner, gateway_ip, 5)
    net_stats = await asyncio.to_thread(runner, "1.1.1.1", 5)
    udp_drops = check_udp_drops()

    metrics: dict[str, Any] = {
        "gateway_ip": gateway_ip,
        "gw_avg": gw_stats[0],
        "gw_jitter": gw_stats[1],
        "gw_loss": gw_stats[2],
        "net_avg": net_stats[0],
        "net_jitter": net_stats[1],
        "net_loss": net_stats[2],
        "server_fps": server_fps,
        "server_frame_time_ms": server_frame_time_ms,
        "udp_drops": udp_drops,
        "nat_aligned": nat_aligned,
    }

    verdict_info = evaluate_diagnostics_verdict(metrics)

    return {
        "gateway_ip": gateway_ip,
        "gateway_ping_avg_ms": gw_stats[0],
        "gateway_jitter_ms": gw_stats[1],
        "gateway_packet_loss_pct": gw_stats[2],
        "internet_ping_avg_ms": net_stats[0],
        "internet_jitter_ms": net_stats[1],
        "internet_packet_loss_pct": net_stats[2],
        "server_fps": server_fps,
        "server_frame_time_ms": server_frame_time_ms,
        "udp_drops_detected": udp_drops,
        "nat_aligned": nat_aligned,
        "verdict": verdict_info[0],
        "verdict_title": verdict_info[1],
        "verdict_details": verdict_info[2],
        "recommendation": verdict_info[3],
    }
