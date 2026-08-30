"""Community Discovery Hub & Bare-Metal Telemetry Engine.

Provides multi-layer discovery tracking, Valve Steam A2S UDP packet queries,
Pocketpair Master Server directory probes, EOS Session ID journal scraping,
and bare-metal resource monitoring in accordance with 3 AM standards.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import socket
import subprocess
import time
from typing import Any

import httpx
import psutil

from .logger import log

DEFAULT_LEDGER_PATH = (
    "/var/lib/palmanager/players.json" if os.name != "nt" else os.path.expanduser("~/.palmanager/players.json")
)


class CommunityTracker:
    """Probes Palworld community listings, A2S UDP sockets, and bare-metal resource telemetry."""

    def __init__(
        self,
        server_name: str | None = None,
        domain: str | None = None,
        player_ledger_path: str | None = None,
    ) -> None:
        self.server_name: str = server_name or os.getenv("PALWORLD_SERVER_NAME") or "Palworld Dedicated Server"
        self.domain: str = domain or os.getenv("PALWORLD_SERVER_DOMAIN") or "yourdomain.duckdns.org"
        self.player_ledger_path: str = player_ledger_path or os.getenv("PLAYER_LEDGER_PATH") or DEFAULT_LEDGER_PATH

        # 1. Local Log Scraper State (EOS Public Session ID)
        self.log_registered: bool = False
        self.log_first_seen: str | None = None
        self.log_session_id: str | None = None
        self.log_last_matched_line: str = "Awaiting initial engine log lines..."

        # 2. Pocketpair Master Server Directory Probe State
        self.pocketpair_listed: bool = False
        self.pocketpair_server_id: str = "Unlisted"
        self.pocketpair_name: str = self.server_name
        self.pocketpair_version: str = "Unknown"
        self.last_pocketpair_check: float = 0.0

        # 3. Valve Steam A2S_INFO UDP Query State
        self.a2s_ping_ms: float | None = None
        self.a2s_responsive: bool = False
        self.a2s_server_name: str | None = None
        self.a2s_map_name: str | None = None
        self.a2s_players: int = 0
        self.a2s_max_players: int = 32

        # 4. Network & DNS Alignment State
        self.cached_public_ip: str = "Detecting..."
        self.cached_dns_ip: str = "Resolving..."
        self.last_ip_check: float = 0.0

        # 5. Persistent Player Ledger
        self.players_history: dict[str, Any] = self._load_player_ledger()

    def _load_player_ledger(self) -> dict[str, Any]:
        """Loads historical player roster from persistent JSON storage."""
        if os.path.exists(self.player_ledger_path):
            try:
                with open(self.player_ledger_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to parse player ledger from %s: %s", self.player_ledger_path, e)
        return {}

    def _save_player_ledger(self) -> None:
        """Persists current player roster to disk."""
        try:
            os.makedirs(os.path.dirname(self.player_ledger_path), exist_ok=True)
            with open(self.player_ledger_path, "w", encoding="utf-8") as f:
                json.dump(self.players_history, f, indent=2)
        except OSError as e:
            log.warning("Failed to write player ledger to %s: %s", self.player_ledger_path, e)

    @staticmethod
    def parse_a2s_packet(data: bytes, latency_ms: float | None = None) -> dict[str, Any]:
        """Parses a Valve A2S_INFO binary UDP response packet."""
        if len(data) > 6 and data[:4] == b"\xff\xff\xff\xff" and data[4] == 0x49:
            body = data[6:]
            parts = body.split(b"\x00")
            server_name = parts[0].decode("utf-8", errors="ignore") if len(parts) > 0 else ""
            map_name = parts[1].decode("utf-8", errors="ignore") if len(parts) > 1 else ""
            folder = parts[2].decode("utf-8", errors="ignore") if len(parts) > 2 else ""
            game = parts[3].decode("utf-8", errors="ignore") if len(parts) > 3 else ""

            return {
                "responsive": True,
                "ping_ms": latency_ms,
                "server_name": server_name,
                "map_name": map_name,
                "folder": folder,
                "game": game,
            }
        return {"responsive": bool(data), "ping_ms": latency_ms}

    async def probe_steam_a2s_info(self, host: str = "127.0.0.1", port: int = 8211) -> dict[str, Any]:
        """Direct UDP A2S_INFO packet query to the server socket (measures raw latency in ms)."""
        query_packet = b"\xff\xff\xff\xffTSource Engine Query\x00"
        loop = asyncio.get_running_loop()

        def _sync_a2s_query() -> dict[str, Any]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1.5)
            t_start = time.perf_counter()
            try:
                sock.sendto(query_packet, (host, port))
                data, _ = sock.recvfrom(2048)
                t_end = time.perf_counter()
                latency_ms = round((t_end - t_start) * 1000, 1)
                return self.parse_a2s_packet(data, latency_ms)
            except (TimeoutError, OSError) as e:
                log.debug("A2S socket query failed on %s:%s: %s", host, port, e)
                return {"responsive": False, "ping_ms": None}
            finally:
                sock.close()

        try:
            res = await loop.run_in_executor(None, _sync_a2s_query)
            self.a2s_responsive = bool(res.get("responsive"))
            self.a2s_ping_ms = res.get("ping_ms")
            if res.get("server_name"):
                self.a2s_server_name = res["server_name"]
            if res.get("map_name"):
                self.a2s_map_name = res["map_name"]
            return res
        except Exception as e:
            log.debug("A2S async executor exception: %s", e)
            self.a2s_responsive = False
            self.a2s_ping_ms = None
            return {"responsive": False, "ping_ms": None}

    async def probe_pocketpair_master_list(self) -> None:
        """Probes Pocketpair's public master server directory API to verify official server listing."""
        if time.time() - self.last_pocketpair_check < 120.0 and self.pocketpair_listed:
            return

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get("https://palworld-server-api.pocketpair.jp/v1/server/list")
                if res.status_code == 200:
                    data = res.json()
                    servers = data.get("servers", [])
                    clean_target = (self.server_name or "").lower().strip()
                    for s in servers:
                        s_name = s.get("name", "").lower().strip()
                        if clean_target and clean_target in s_name:
                            self.pocketpair_listed = True
                            self.pocketpair_server_id = s.get("server_id", "Listed")
                            self.pocketpair_name = s.get("name", self.server_name)
                            self.pocketpair_version = s.get("version", "Live")
                            self.last_pocketpair_check = time.time()
                            return
        except httpx.HTTPError as e:
            log.debug("Pocketpair directory probe exception: %s", e)

        self.last_pocketpair_check = time.time()

    def probe_local_logs(self) -> dict[str, Any]:
        """Scrapes systemd journalctl for Palworld EOS session & lobby initialization lines."""
        if self.log_registered and self.log_session_id:
            return {
                "registered": True,
                "first_seen": self.log_first_seen,
                "session_id": self.log_session_id,
                "last_line": self.log_last_matched_line,
            }

        if os.name != "nt":
            try:
                cmd = ["sudo", "journalctl", "-u", "palworld.service", "-n", "300", "--no-pager"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
                if proc.returncode == 0:
                    pattern = (
                        r"Created public lobby session|EOS-SDK.*sessions|"
                        r"Lobby.*Registered|PublicSession|Steam server initialized"
                    )
                    for line in proc.stdout.splitlines():
                        if re.search(pattern, line, re.IGNORECASE):
                            self.log_registered = True
                            self.log_last_matched_line = line.strip()

                            match = re.search(r"SessionId:\s*([A-Za-z0-9_\-]+)", line)
                            if match:
                                self.log_session_id = match.group(1)

                            if not self.log_first_seen:
                                self.log_first_seen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                            return {
                                "registered": True,
                                "session_id": self.log_session_id,
                                "first_seen": self.log_first_seen,
                                "last_line": self.log_last_matched_line,
                            }
            except (subprocess.SubprocessError, OSError) as e:
                log.debug("Journalctl probe failed: %s", e)

        return {
            "registered": self.log_registered,
            "session_id": self.log_session_id,
            "first_seen": self.log_first_seen,
            "last_line": self.log_last_matched_line,
        }

    def probe_network_alignment(self) -> dict[str, Any]:
        """Probes WAN public IP and DuckDNS resolution to verify network alignment."""
        try:
            with httpx.Client(timeout=2.0) as client:
                res = client.get("https://api.ipify.org?format=json")
                if res.status_code == 200:
                    self.cached_public_ip = res.json().get("ip", "Unknown")
        except httpx.HTTPError as e:
            log.debug("WAN IP detection failed: %s", e)

        try:
            if self.domain:
                self.cached_dns_ip = socket.gethostbyname(str(self.domain))
            else:
                self.cached_dns_ip = "Unresolved"
        except (socket.gaierror, OSError) as e:
            log.debug("DNS resolution failed for %s: %s", self.domain, e)
            self.cached_dns_ip = "Unresolved"

        is_aligned = bool(
            self.cached_public_ip and self.cached_dns_ip != "Unresolved" and self.cached_public_ip == self.cached_dns_ip
        )

        return {
            "public_ip": self.cached_public_ip or "Unknown",
            "dns_ip": self.cached_dns_ip or "Unresolved",
            "domain": self.domain,
            "is_aligned": is_aligned,
        }

    def update_and_get_players(self, live_players_raw: list[dict[str, Any]]) -> dict[str, Any]:
        """Merges live player telemetry into persistent roster ledger."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        active_list = []
        active_ids = set()

        for p in live_players_raw:
            pid = str(p.get("playerId", ""))
            if not pid:
                continue
            active_ids.add(pid)

            player_record = {
                "playerId": pid,
                "userId": p.get("userId", ""),
                "name": p.get("name", "Unknown Tamer"),
                "level": p.get("level", 1),
                "ping": p.get("ping", 0.0),
                "location": {
                    "x": p.get("location_x", 0.0),
                    "y": p.get("location_y", 0.0),
                },
                "status": "ONLINE",
                "last_seen": now_str,
            }

            self.players_history[pid] = player_record
            active_list.append(player_record)

        offline_list = []
        for pid, record in self.players_history.items():
            if pid not in active_ids:
                record["status"] = "OFFLINE"
                offline_list.append(record)

        self._save_player_ledger()

        return {
            "active_count": len(active_list),
            "total_registered": len(self.players_history),
            "active_players": active_list,
            "offline_players": offline_list,
        }

    def get_hardware_telemetry(self) -> dict[str, Any]:
        """Collects bare-metal hardware and systemd cgroup 14GB allocation metrics."""
        cgroup_ram_bytes = 0
        cgroup_path = "/sys/fs/cgroup/system.slice/palworld.service/memory.current"
        if os.name != "nt" and os.path.exists(cgroup_path):
            try:
                with open(cgroup_path, encoding="utf-8") as f:
                    cgroup_ram_bytes = int(f.read().strip())
            except (OSError, ValueError) as e:
                log.debug("Failed reading cgroup memory.current: %s", e)

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        cpu_pcts = psutil.cpu_percent(percpu=True)
        net_all = psutil.net_io_counters(pernic=True)
        net = net_all.get("eth0", psutil.net_io_counters())

        cgroup_ram_gb = round(cgroup_ram_bytes / (1024**3), 2)
        cgroup_limit_gb = 14.0
        cgroup_ram_pct = round((cgroup_ram_gb / cgroup_limit_gb) * 100, 1)

        disk_path = "/" if os.name != "nt" else "C:\\"
        try:
            disk = psutil.disk_usage(disk_path)
            disk_used_gb = round(disk.used / (1024**3), 1)
            disk_total_gb = round(disk.total / (1024**3), 1)
            disk_pct = round(disk.percent, 1)
        except OSError:
            disk_used_gb, disk_total_gb, disk_pct = 0.0, 0.0, 0.0

        return {
            "host_ram_used_gb": round(vm.used / (1024**3), 2),
            "host_ram_total_gb": round(vm.total / (1024**3), 2),
            "host_ram_pct": round(vm.percent, 1),
            "cgroup_ram_used_gb": cgroup_ram_gb,
            "cgroup_limit_gb": cgroup_limit_gb,
            "cgroup_ram_pct": min(cgroup_ram_pct, 100.0),
            "swap_used_gb": round(swap.used / (1024**3), 2),
            "swap_total_gb": round(swap.total / (1024**3), 2),
            "swap_pct": round(swap.percent, 1),
            "cpu_cores": [round(c, 1) for c in cpu_pcts],
            "cpu_avg_pct": round(sum(cpu_pcts) / len(cpu_pcts), 1) if cpu_pcts else 0.0,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "disk_pct": disk_pct,
            "net_bytes_sent": net.bytes_sent,
            "net_bytes_recv": net.bytes_recv,
        }

    async def get_combined_telemetry(
        self,
        is_multiplay: bool = True,
        host_ip: str | None = None,
        public_port: int = 8211,
        server_password: str = "",
        rcon_port: int = 25575,
        rest_port: int = 8212,
        max_players: int = 32,
        current_players: int = 0,
    ) -> dict[str, Any]:
        """Assembles full 3-section telemetry matrix for the reactive UI dashboard."""
        self.probe_network_alignment()
        await self.probe_pocketpair_master_list()
        await self.probe_steam_a2s_info(host=host_ip or "127.0.0.1", port=public_port)
        log_res = self.probe_local_logs()

        lan_ip = host_ip or os.getenv("PALWORLD_HOST_IP", "127.0.0.1")

        direct_connect_host = (
            self.domain
            if (self.domain and "yourdomain" not in self.domain)
            else (self.cached_public_ip if self.cached_public_ip != "Detecting..." else lan_ip)
        )

        # Header Badge
        if self.pocketpair_listed:
            badge_label = "Community Listed"
            badge_style = "bg-emerald-900/60 border border-emerald-500/50 text-emerald-300 font-bold"
            badge_dot = "bg-emerald-400"
        elif log_res.get("registered"):
            badge_label = "Console Search Ready"
            badge_style = "bg-brand-900/60 border border-brand-500/50 text-brand-300 font-bold"
            badge_dot = "bg-brand-400 animate-pulse"
        elif not is_multiplay:
            badge_label = "Private Server"
            badge_style = "bg-slate-800 border border-slate-700 text-slate-300"
            badge_dot = "bg-slate-400"
        else:
            badge_label = f"Direct Connect ({lan_ip}:{public_port})"
            badge_style = "bg-amber-900/60 border border-amber-500/50 text-amber-300"
            badge_dot = "bg-amber-400"

        return {
            "top_badge": {
                "label": badge_label,
                "style": badge_style,
                "dot": badge_dot,
            },
            "discovery_hub": {
                "log_scraper": {
                    "registered": log_res.get("registered", False),
                    "session_id": log_res.get("session_id"),
                    "first_seen": log_res.get("first_seen"),
                    "last_line": log_res.get("last_line", ""),
                    "status_label": "CONSOLE SEARCH READY" if log_res.get("registered") else "AWAITING EOS HANDSHAKE",
                    "status_color": "emerald" if log_res.get("registered") else "amber",
                    "crossplay_platforms": "(Steam, Xbox, PS5, Mac)",
                },
                "pocketpair_master": {
                    "listed": self.pocketpair_listed,
                    "server_id": self.pocketpair_server_id,
                    "name": self.pocketpair_name,
                    "version": self.pocketpair_version,
                    "status_label": "OFFICIALLY LISTED" if self.pocketpair_listed else "COMMUNITY / DIRECT MODE",
                    "status_color": "emerald" if self.pocketpair_listed else "amber",
                },
                "network_matrix": {
                    "public_ip": self.cached_public_ip,
                    "dns_ip": self.cached_dns_ip,
                    "domain": self.domain,
                    "is_aligned": bool(self.cached_public_ip == self.cached_dns_ip),
                    "lan_ip": lan_ip,
                    "public_port": public_port,
                    "direct_connect_addr": f"{direct_connect_host}:{public_port}",
                    "lan_connect_addr": f"{lan_ip}:{public_port}",
                },
            },
            "a2s_telemetry": {
                "responsive": self.a2s_responsive,
                "ping_ms": self.a2s_ping_ms,
                "server_name": self.a2s_server_name or self.server_name,
                "map_name": self.a2s_map_name or "Pal/Maps/World",
                "folder": "Pal",
                "game": "Palworld",
            },
            "security_matrix": {
                "is_password_protected": bool(server_password),
                "password_status_label": "🔒 Password Protected"
                if server_password
                else "🔓 Public Access (No Password)",
                "server_password": server_password,
                "rcon_port": rcon_port,
                "rest_port": rest_port,
                "max_players": max_players,
                "current_players": current_players,
                "slot_capacity_label": f"{current_players} / {max_players} Tamers",
            },
        }
