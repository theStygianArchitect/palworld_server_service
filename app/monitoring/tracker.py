"""Palworld Community Listings, Steam A2S UDP Probing, and Telemetry Hub.

Aggregates Pocketpair master server directory status, Valve A2S UDP packet queries,
bare-metal system resource metrics, and player ledger persistence in compliance with
the 3 AM Debugger Standard.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import socket
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from app.core.config import is_posix, resolve_host_lan_ip
from app.core.logger import log
from app.core.types import (
    CombinedTelemetryPayload,
    DiscoveryHubPayload,
    HardwareTelemetryInfo,
    LogScraperInfo,
    NetworkDiagnosticsResult,
    NetworkMatrixInfo,
    PlayerLedgerMatrix,
    PlayerRecord,
    PocketpairMasterInfo,
    SecurityMatrixInfo,
    SteamA2SInfo,
    TopBadgeInfo,
)

from .log_scraper import PalLogScraper
from .net_diagnostics import (
    NetworkThroughputTracker,
    run_network_diagnostics_sweep,
)
from .net_diagnostics import (
    execute_ping_probes as _execute_ping_probes,
)

DEFAULT_LEDGER_PATH: Path = (
    Path("/var/lib/palmanager/players_history.json")
    if os.name != "nt"
    else Path.home() / ".palmanager" / "players_history.json"
)

A2S_INFO_REQUEST: bytes = b"\xff\xff\xff\xff\x54Source Engine Query\x00"


class CommunityTracker:
    """Probes Palworld community listings, A2S UDP sockets, and bare-metal resource telemetry.

    Attributes:
        identity (dict[str, Any]): Configured server identity dictionary.
        scraper (PalLogScraper): Engine log tailer and session scraper.
        pocketpair (dict[str, Any]): Official Pocketpair directory probe state.
        a2s (dict[str, Any]): Valve Steam A2S query state.
        dns (dict[str, Any]): External WAN and DNS resolution state.
        net_traffic (NetworkThroughputTracker): Rate calculation engine.
        players_history (dict[str, Any]): Player roster ledger dictionary.
    """

    def __init__(
        self,
        server_name: str | None = None,
        domain: str | None = None,
        player_ledger_path: str | Path | None = None,
        session_cache_path: str | Path | None = None,
    ) -> None:
        """Initializes the CommunityTracker with server identity and storage paths.

        Args:
            server_name (str | None): Server name for directory matching.
            domain (str | None): DuckDNS domain for DNS resolution.
            player_ledger_path (str | Path | None): Filepath for persistent player ledger.
            session_cache_path (str | Path | None): Filepath for persistent EOS session cache.
        """
        resolved_name = server_name or os.getenv("PALWORLD_SERVER_NAME") or "Palworld Dedicated Server"
        raw_domain = (
            domain
            or os.getenv("PALWORLD_DOMAIN")
            or os.getenv("PALWORLD_SERVER_DOMAIN")
            or os.getenv("DUCKDNS_DOMAIN")
            or "yourdomain.duckdns.org"
        )
        clean_domain = raw_domain.split(":")[0].strip()
        if clean_domain and "." not in clean_domain and clean_domain != "yourdomain":
            clean_domain = f"{clean_domain}.duckdns.org"

        if player_ledger_path is not None:
            resolved_ledger = Path(player_ledger_path)
        else:
            env_ledger = os.getenv("PLAYER_LEDGER_PATH")
            resolved_ledger = Path(env_ledger) if env_ledger else DEFAULT_LEDGER_PATH

        self.identity: dict[str, Any] = {
            "server_name": resolved_name,
            "domain": clean_domain,
            "player_ledger_path": resolved_ledger,
        }
        self.pocketpair: dict[str, Any] = {
            "listed": False,
            "server_id": "Unlisted",
            "name": resolved_name,
            "version": "Unknown",
            "last_check": 0.0,
        }
        self.a2s: dict[str, Any] = {
            "ping_ms": None,
            "responsive": False,
            "server_name": None,
            "map_name": None,
            "players": 0,
            "max_players": 32,
            "query_port": 27015,
        }
        self.dns: dict[str, Any] = {
            "cached_public_ip": "Detecting...",
            "cached_dns_ip": "Resolving...",
            "last_ip_check": 0.0,
            "last_auto_duckdns_sync": 0.0,
        }
        self.scraper: PalLogScraper = PalLogScraper(session_cache_path)
        self.net_traffic: NetworkThroughputTracker = NetworkThroughputTracker()
        self.players_history: dict[str, Any] = {}

        self._load_player_ledger()

    def __getattr__(self, name: str) -> Any:
        """Dynamically proxies legacy attribute accesses to underlying state containers."""
        d = self.__dict__
        if name.startswith("log_") and "scraper" in d:
            scraper_attr = name[4:]
            if hasattr(d["scraper"], scraper_attr):
                return getattr(d["scraper"], scraper_attr)
        if name.startswith("pocketpair_") and "pocketpair" in d:
            pocket_key = name[11:]
            if pocket_key in d["pocketpair"]:
                return d["pocketpair"][pocket_key]
        if name.startswith("a2s_") and "a2s" in d:
            a2s_key = name[4:]
            if a2s_key in d["a2s"]:
                return d["a2s"][a2s_key]
        if "dns" in d and name in d["dns"]:
            return d["dns"][name]
        if "identity" in d and name in d["identity"]:
            return d["identity"][name]
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        """Dynamically routes legacy attribute writes to underlying state containers."""
        d = self.__dict__
        if name.startswith("log_") and "scraper" in d:
            scraper_attr = name[4:]
            if hasattr(d["scraper"], scraper_attr):
                setattr(d["scraper"], scraper_attr, value)
                return
        if name.startswith("pocketpair_") and "pocketpair" in d:
            pocket_key = name[11:]
            if pocket_key in d["pocketpair"]:
                d["pocketpair"][pocket_key] = value
                return
        if name.startswith("a2s_") and "a2s" in d:
            a2s_key = name[4:]
            if a2s_key in d["a2s"]:
                d["a2s"][a2s_key] = value
                return
        if "dns" in d and name in d["dns"]:
            d["dns"][name] = value
            return
        if "identity" in d and name in d["identity"]:
            d["identity"][name] = value
            return
        super().__setattr__(name, value)

    def _load_player_ledger(self) -> None:
        """Loads historical player roster from JSON file on disk."""
        ledger_path: Path = self.identity["player_ledger_path"]
        if not ledger_path.is_file():
            return
        try:
            raw_text = ledger_path.read_text(encoding="utf-8")
            self.players_history = json.loads(raw_text)
        except PermissionError as err:
            log.warning("Permission denied reading player ledger from %s: %s", ledger_path, err)
        except json.JSONDecodeError as err:
            log.warning("Corrupted JSON in player ledger file %s: %s", ledger_path, err)
        except OSError as err:
            log.warning("OS error loading player ledger from %s: %s", ledger_path, err)

    def _save_player_ledger(self) -> None:
        """Saves current player roster to disk atomically."""
        ledger_path: Path = self.identity["player_ledger_path"]
        try:
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = ledger_path.with_suffix(".tmp")
            temp_file.write_text(json.dumps(self.players_history, indent=2), encoding="utf-8")
            temp_file.replace(ledger_path)
        except PermissionError as err:
            log.warning("Permission denied writing player ledger to %s: %s", ledger_path, err)
        except OSError as err:
            log.warning("OS error saving player ledger to %s: %s", ledger_path, err)

    @staticmethod
    def parse_a2s_packet(
        data: bytes,
        latency_ms: float | None = None,
        query_port: int = 27015,
    ) -> SteamA2SInfo:
        """Parses a Valve A2S_INFO binary UDP response packet.

        Args:
            data (bytes): Raw payload bytes received from UDP socket.
            latency_ms (float | None): Measured round-trip ping in milliseconds.
            query_port (int): Target UDP query port probed.

        Returns:
            SteamA2SInfo: Structured game server info extracted from binary packet.
        """
        fallback: SteamA2SInfo = {
            "responsive": False,
            "ping_ms": None,
            "server_name": "",
            "map_name": "",
            "folder": "",
            "game": "",
            "query_port": query_port,
            "players": 0,
            "max_players": 32,
        }
        if len(data) < 6 or data[:4] != b"\xff\xff\xff\xff" or data[4] != 0x49:
            return fallback

        offset = 6
        fields: list[str] = []
        for _ in range(4):
            null_pos = data.find(b"\x00", offset)
            if null_pos == -1:
                break
            fields.append(data[offset:null_pos].decode("utf-8", errors="replace"))
            offset = null_pos + 1

        players, max_players = (int(data[offset + 2]), int(data[offset + 3])) if len(data) >= offset + 4 else (0, 32)

        return {
            "responsive": True,
            "ping_ms": latency_ms,
            "server_name": fields[0] if len(fields) > 0 else "Palworld Dedicated Server",
            "map_name": fields[1] if len(fields) > 1 else "Pal/Maps/World",
            "folder": fields[2] if len(fields) > 2 else "Pal",
            "game": fields[3] if len(fields) > 3 else "Palworld",
            "query_port": query_port,
            "players": players,
            "max_players": max_players,
        }

    @classmethod
    def _execute_a2s_socket_query(cls, target_ip: str, target_port: int) -> SteamA2SInfo | None:
        """Executes a single synchronous UDP A2S query against target IP and port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.8)
        t0 = time.perf_counter()
        try:
            sock.sendto(A2S_INFO_REQUEST, (target_ip, target_port))
            resp_data, _ = sock.recvfrom(2048)
            latency = round((time.perf_counter() - t0) * 1000.0, 1)

            if len(resp_data) >= 9 and resp_data[4] == 0x41:
                challenge = resp_data[5:9]
                t0 = time.perf_counter()
                sock.sendto(b"\xff\xff\xff\xff\x54Source Engine Query\x00" + challenge, (target_ip, target_port))
                resp_data, _ = sock.recvfrom(2048)
                latency = round((time.perf_counter() - t0) * 1000.0, 1)

            parsed = cls.parse_a2s_packet(resp_data, latency_ms=latency, query_port=target_port)
            if parsed.get("responsive"):
                return parsed
        except TimeoutError as err:
            log.debug("A2S UDP query timed out on %s:%s: %s", target_ip, target_port, err)
        except OSError as err:
            log.debug("A2S UDP socket error on %s:%s: %s", target_ip, target_port, err)
        finally:
            sock.close()
        return None

    async def probe_steam_a2s_info(
        self,
        host: str = "127.0.0.1",
        port: int = 27015,
        candidate_ports: list[int] | None = None,
    ) -> SteamA2SInfo:
        """Queries the Valve Steam A2S UDP server query socket across candidate ports.

        Args:
            host (str): IP address or hostname to query.
            port (int): Target UDP query port.
            candidate_ports (list[int] | None): Candidate ports to probe in priority order.

        Returns:
            SteamA2SInfo: Parsed response or empty dict if offline/unreachable.
        """
        loop = asyncio.get_running_loop()
        target_hosts = ["127.0.0.1"]
        if host and host not in ("127.0.0.1", "localhost") and host not in target_hosts:
            target_hosts.append(host)

        probed_ports: list[int] = []
        for candidate in candidate_ports or [port, 27015, 8211]:
            if candidate not in probed_ports:
                probed_ports.append(candidate)

        def _sweep_candidates() -> SteamA2SInfo | None:
            for tip in target_hosts:
                for tport in probed_ports:
                    res = self._execute_a2s_socket_query(tip, tport)
                    if res is not None:
                        return res
            return None

        fallback: SteamA2SInfo = {
            "responsive": False,
            "ping_ms": None,
            "server_name": "",
            "map_name": "",
            "folder": "",
            "game": "",
            "query_port": port,
            "players": 0,
            "max_players": 32,
        }

        try:
            res = await loop.run_in_executor(None, _sweep_candidates)
            if res is not None:
                self.a2s["responsive"] = True
                self.a2s["ping_ms"] = res.get("ping_ms")
                self.a2s["query_port"] = res.get("query_port", port)
                self.a2s["players"] = res.get("players", 0)
                self.a2s["max_players"] = res.get("max_players", 32)
                if res.get("server_name"):
                    self.a2s["server_name"] = res["server_name"]
                if res.get("map_name"):
                    self.a2s["map_name"] = res["map_name"]
                return res
        except TimeoutError as err:
            log.debug("A2S executor timeout error: %s", err)
        except RuntimeError as err:
            log.debug("A2S executor runtime error: %s", err)
        except OSError as err:
            log.debug("A2S executor OS error: %s", err)
        except ValueError as err:
            log.debug("A2S executor value error: %s", err)

        self.a2s["responsive"] = False
        self.a2s["ping_ms"] = None
        return fallback

    async def probe_pocketpair_master_list(self) -> None:
        """Probes Pocketpair's public master server directory API to verify official server listing."""
        if time.time() - self.pocketpair["last_check"] < 45.0:
            return

        url = "https://pal-conf.pocketpair.jp/api/server/list"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    servers = data.get("servers", [])
                    matched = False
                    for s in servers:
                        s_name = s.get("name", "")
                        if self.server_name.lower() in s_name.lower():
                            self.pocketpair["listed"] = True
                            self.pocketpair["server_id"] = s.get("serverId", s.get("id", "Listed"))
                            self.pocketpair["name"] = s_name
                            self.pocketpair["version"] = s.get("version", "Unknown")
                            matched = True
                            break
                    if not matched:
                        self.pocketpair["listed"] = False
                        self.pocketpair["server_id"] = "Unlisted"
        except httpx.TimeoutException as err:
            log.debug("Pocketpair directory probe timed out: %s", err)
        except httpx.ConnectError as err:
            log.debug("Pocketpair directory probe connection failed: %s", err)
        except httpx.HTTPError as err:
            log.debug("Pocketpair directory probe HTTP error: %s", err)

        self.pocketpair["last_check"] = time.time()

    def probe_local_logs(self) -> LogScraperInfo:
        """Performs real-time and retroactive log scraping across Pal.log files and journalctl."""
        return self.scraper.probe_local_logs(is_posix_checker=is_posix)

    def read_server_logs(
        self,
        tail: int = 200,
        filter_query: str | None = None,
        level: str = "ALL",
    ) -> dict[str, Any]:
        """Reads, filters, and sanitizes recent engine log lines for the live console."""
        return self.scraper.read_server_logs(tail=tail, filter_query=filter_query, level=level)

    def probe_network_alignment(self) -> NetworkMatrixInfo:
        """Probes WAN public IP and DuckDNS resolution to verify network alignment.

        Returns:
            NetworkMatrixInfo: Public IP, resolved DNS IP, and alignment boolean status.
        """
        try:
            with httpx.Client(timeout=2.0) as client:
                res = client.get("https://api.ipify.org?format=json")
                if res.status_code == 200:
                    self.dns["cached_public_ip"] = res.json().get("ip", "Unknown")
        except httpx.TimeoutException as err:
            log.debug("WAN IP detection timed out: %s", err)
        except httpx.ConnectError as err:
            log.debug("WAN IP connection failed: %s", err)
        except httpx.HTTPError as err:
            log.debug("WAN IP HTTP error: %s", err)

        try:
            if self.domain:
                self.dns["cached_dns_ip"] = socket.gethostbyname(str(self.domain))
            else:
                self.dns["cached_dns_ip"] = "Unresolved"
        except socket.gaierror as err:
            log.debug("DNS address resolution failed for %s: %s", self.domain, err)
            self.dns["cached_dns_ip"] = "Unresolved"
        except OSError as err:
            log.debug("Socket OS error resolving %s: %s", self.domain, err)
            self.dns["cached_dns_ip"] = "Unresolved"

        is_aligned = bool(
            self.dns["cached_public_ip"]
            and self.dns["cached_public_ip"] != "Detecting..."
            and self.dns["cached_public_ip"] != "Unknown"
            and self.dns["cached_dns_ip"] != "Unresolved"
            and self.dns["cached_public_ip"] == self.dns["cached_dns_ip"]
        )

        lan_ip = resolve_host_lan_ip()
        public_port = 8211
        direct_host = self.domain if (self.domain and "yourdomain" not in self.domain) else self.dns["cached_public_ip"]

        return {
            "public_ip": self.dns["cached_public_ip"] or "Unknown",
            "dns_ip": self.dns["cached_dns_ip"] or "Unresolved",
            "domain": self.domain,
            "is_aligned": is_aligned,
            "lan_ip": lan_ip,
            "public_port": public_port,
            "direct_connect_addr": f"{direct_host}:{public_port}",
            "lan_connect_addr": f"{lan_ip}:{public_port}",
        }

    def update_and_get_players(self, live_players_raw: list[dict[str, Any]]) -> PlayerLedgerMatrix:
        """Merges live player telemetry into persistent roster ledger.

        Args:
            live_players_raw (list[dict[str, Any]]): Raw player dictionaries from REST API.

        Returns:
            PlayerLedgerMatrix: Summary counts and player records.
        """
        now_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        active_ids: set[str] = set()
        active_list: list[PlayerRecord] = []

        for p in live_players_raw:
            pid = str(p.get("playerId") or p.get("userId") or "Unknown")
            active_ids.add(pid)
            name = p.get("name") or p.get("accountName") or "Tamer"

            if pid not in self.players_history:
                self.players_history[pid] = {
                    "playerId": pid,
                    "userId": p.get("userId", ""),
                    "name": name,
                    "first_seen": now_ts,
                    "last_seen": now_ts,
                    "level": p.get("level", 1),
                    "ping": p.get("ping", 0.0),
                    "location_x": p.get("location_x", 0.0),
                    "location_y": p.get("location_y", 0.0),
                }
            else:
                record = self.players_history[pid]
                record["name"] = name
                record["last_seen"] = now_ts
                record["level"] = p.get("level", record.get("level", 1))
                record["ping"] = p.get("ping", record.get("ping", 0.0))
                record["location_x"] = p.get("location_x", record.get("location_x", 0.0))
                record["location_y"] = p.get("location_y", record.get("location_y", 0.0))

            active_list.append(
                {
                    "playerId": pid,
                    "userId": self.players_history[pid].get("userId", ""),
                    "name": name,
                    "level": self.players_history[pid]["level"],
                    "ping": self.players_history[pid]["ping"],
                    "location_x": self.players_history[pid]["location_x"],
                    "location_y": self.players_history[pid]["location_y"],
                    "first_seen": self.players_history[pid]["first_seen"],
                    "last_seen": now_ts,
                    "status": "ONLINE",
                }
            )

        offline_list: list[PlayerRecord] = []
        for pid, rec in self.players_history.items():
            if pid not in active_ids:
                offline_list.append(
                    {
                        "playerId": pid,
                        "userId": rec.get("userId", ""),
                        "name": rec.get("name", "Tamer"),
                        "level": rec.get("level", 1),
                        "ping": 0.0,
                        "location_x": rec.get("location_x", 0.0),
                        "location_y": rec.get("location_y", 0.0),
                        "first_seen": rec.get("first_seen", now_ts),
                        "last_seen": rec.get("last_seen", now_ts),
                        "status": "OFFLINE",
                    }
                )

        if active_list:
            self._save_player_ledger()

        return {
            "active_count": len(active_list),
            "total_registered": len(self.players_history),
            "active_players": active_list,
            "offline_players": offline_list,
        }

    @staticmethod
    def _read_cgroup_memory() -> int:
        """Reads memory allocated to systemd cgroup on Linux hosts."""
        cgroup_path = Path("/sys/fs/cgroup/system.slice/palworld.service/memory.current")
        if not is_posix() or not cgroup_path.exists():
            return 0
        try:
            return int(cgroup_path.read_text(encoding="utf-8").strip())
        except ValueError as err:
            log.debug("Cgroup memory read invalid integer format on %s: %s", cgroup_path, err)
            return 0
        except FileNotFoundError as err:
            log.debug("Cgroup memory file not found on %s: %s", cgroup_path, err)
            return 0
        except PermissionError as err:
            log.debug("Cgroup memory read permission denied on %s: %s", cgroup_path, err)
            return 0
        except OSError as err:
            log.debug("Cgroup memory read OS error on %s: %s", cgroup_path, err)
            return 0

    @staticmethod
    def _read_disk_usage() -> tuple[float, float, float]:
        """Collects disk usage for root filesystem."""
        disk_path = "/" if is_posix() else "C:\\"
        try:
            disk = psutil.disk_usage(disk_path)
            return (round(disk.used / (1024**3), 1), round(disk.total / (1024**3), 1), round(disk.percent, 1))
        except OSError as err:
            log.debug("Disk usage telemetry failed for %s: %s", disk_path, err)
            return (0.0, 0.0, 0.0)

    def get_hardware_telemetry(self) -> HardwareTelemetryInfo:
        """Collects bare-metal hardware and systemd cgroup 14GB allocation metrics.

        Returns:
            HardwareTelemetryInfo: System RAM, cgroup usage, swap, CPU, disk, and network stats.
        """
        cgroup_ram_bytes = self._read_cgroup_memory()
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        cpu_pcts = psutil.cpu_percent(percpu=True)
        net_all = psutil.net_io_counters(pernic=True)
        net = net_all.get("eth0", psutil.net_io_counters())

        cgroup_ram_gb = round(cgroup_ram_bytes / (1024**3), 2)
        cgroup_limit_gb = 14.0
        cgroup_ram_pct = round((cgroup_ram_gb / cgroup_limit_gb) * 100, 1)

        disk_used_gb, disk_total_gb, disk_pct = self._read_disk_usage()
        rates = self.net_traffic.calculate_rates(net)

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
            "net_rx_rate_kbps": rates["rx_rate_kbps"],
            "net_tx_rate_kbps": rates["tx_rate_kbps"],
            "net_rx_rate_mbps": rates["rx_rate_mbps"],
            "net_tx_rate_mbps": rates["tx_rate_mbps"],
            "net_dropin": int(getattr(net, "dropin", 0)),
            "net_dropout": int(getattr(net, "dropout", 0)),
            "net_errin": int(getattr(net, "errin", 0)),
            "net_errout": int(getattr(net, "errout", 0)),
            "net_traffic_status": rates["status"],
        }

    def _build_top_badge(
        self,
        log_res: LogScraperInfo,
        is_multiplay: bool,
        direct_host: str,
        port: int,
    ) -> TopBadgeInfo:
        """Determines top header badge styling and indicator."""
        if self.pocketpair["listed"]:
            return {
                "label": "Community Listed",
                "style": "bg-emerald-900/60 border border-emerald-500/50 text-emerald-300 font-bold",
                "dot": "bg-emerald-400",
            }
        if log_res.get("registered"):
            return {
                "label": "Console Search Ready",
                "style": "bg-brand-900/60 border border-brand-500/50 text-brand-300 font-bold",
                "dot": "bg-brand-400 animate-pulse",
            }
        if not is_multiplay:
            return {
                "label": "Private Server",
                "style": "bg-slate-800 border border-slate-700 text-slate-300",
                "dot": "bg-slate-400",
            }
        return {
            "label": f"Direct Connect ({direct_host}:{port})",
            "style": "bg-amber-900/60 border border-amber-500/50 text-amber-300",
            "dot": "bg-amber-400",
        }

    def _build_discovery_hub(
        self,
        log_res: LogScraperInfo,
        direct_connect_host: str,
        lan_ip: str,
        public_port: int,
    ) -> DiscoveryHubPayload:
        """Constructs discovery hub payload containing logs, master directory, and IP network matrix."""
        pocketpair_master: PocketpairMasterInfo = {
            "listed": self.pocketpair["listed"],
            "server_id": self.pocketpair["server_id"],
            "name": self.pocketpair["name"],
            "version": self.pocketpair["version"],
            "status_label": "OFFICIALLY LISTED" if self.pocketpair["listed"] else "COMMUNITY / DIRECT MODE",
            "status_color": "emerald" if self.pocketpair["listed"] else "amber",
        }

        network_matrix: NetworkMatrixInfo = {
            "public_ip": self.dns["cached_public_ip"],
            "dns_ip": self.dns["cached_dns_ip"],
            "domain": self.domain,
            "is_aligned": bool(self.dns["cached_public_ip"] == self.dns["cached_dns_ip"]),
            "lan_ip": lan_ip,
            "public_port": public_port,
            "direct_connect_addr": f"{direct_connect_host}:{public_port}",
            "lan_connect_addr": f"{lan_ip}:{public_port}",
        }

        return {
            "log_scraper": log_res,
            "pocketpair_master": pocketpair_master,
            "network_matrix": network_matrix,
        }

    @staticmethod
    def _build_security_matrix(kwargs: dict[str, Any]) -> SecurityMatrixInfo:
        """Extracts and formats security matrix parameters.

        Evaluates the RCON configuration state and emits a deprecation advisory
        when RCON is enabled. Pocketpair deprecated RCON in early 2024 in favour
        of the REST API; operators should disable it via RCONEnabled=False.
        """
        server_password = str(kwargs.get("server_password", ""))
        current_players = int(kwargs.get("current_players", 0))
        max_players = int(kwargs.get("max_players", 32))
        rcon_enabled = bool(kwargs.get("rcon_enabled", True))

        return {
            "is_password_protected": bool(server_password),
            "password_status_label": ("🔒 Password Protected" if server_password else "🔓 Public Access (No Password)"),
            "server_password": server_password,
            "rcon_port": int(kwargs.get("rcon_port", 25575)),
            "rcon_enabled": rcon_enabled,
            "rcon_advisory": "enabled_warn" if rcon_enabled else "disabled_ok",
            "rest_port": int(kwargs.get("rest_port", 8212)),
            "max_players": max_players,
            "current_players": current_players,
            "slot_capacity_label": f"{current_players} / {max_players} Tamers",
        }

    def _build_a2s_telemetry(self) -> SteamA2SInfo:
        """Constructs Steam A2S query telemetry dictionary."""
        return {
            "responsive": self.a2s["responsive"],
            "ping_ms": self.a2s["ping_ms"],
            "server_name": self.a2s["server_name"] or self.server_name,
            "map_name": self.a2s["map_name"] or "Pal/Maps/World",
            "folder": "Pal",
            "game": "Palworld",
            "query_port": int(self.a2s.get("query_port", 27015)),
            "players": int(self.a2s.get("players", 0)),
            "max_players": int(self.a2s.get("max_players", 32)),
        }

    async def get_combined_telemetry(self, **kwargs: Any) -> CombinedTelemetryPayload:
        """Assembles full 3-section telemetry matrix for the reactive UI dashboard.

        Args:
            **kwargs: Dynamic keyword arguments for dashboard telemetry parameters.

        Returns:
            CombinedTelemetryPayload: Complete telemetry schema consumed by dashboard UI.
        """
        host_ip = kwargs.get("host_ip")
        public_port = int(kwargs.get("public_port", 8211))
        query_port = int(kwargs.get("query_port", 27015))
        is_multiplay = bool(kwargs.get("is_multiplay", True))

        self.probe_network_alignment()
        await self.probe_pocketpair_master_list()
        await self.probe_steam_a2s_info(
            host=host_ip or "127.0.0.1",
            port=query_port,
            candidate_ports=[query_port, 27015, public_port],
        )
        log_res = self.probe_local_logs()

        lan_ip = host_ip if (host_ip and host_ip != "127.0.0.1") else resolve_host_lan_ip()
        direct_connect_host = (
            self.domain
            if (self.domain and "yourdomain" not in self.domain)
            else (self.dns["cached_public_ip"] if self.dns["cached_public_ip"] != "Detecting..." else lan_ip)
        )

        return {
            "top_badge": self._build_top_badge(log_res, is_multiplay, direct_connect_host, public_port),
            "discovery_hub": self._build_discovery_hub(log_res, direct_connect_host, lan_ip, public_port),
            "a2s_telemetry": self._build_a2s_telemetry(),
            "security_matrix": self._build_security_matrix(kwargs),
        }

    async def run_network_diagnostics(
        self,
        server_fps: float = 60.0,
        server_frame_time_ms: float = 16.6,
    ) -> NetworkDiagnosticsResult:
        """Executes active network, NAT, and game engine diagnostics to pinpoint rubberbanding root causes.

        Args:
            server_fps (float): Current game engine frame rate.
            server_frame_time_ms (float): Current game engine frame time in milliseconds.

        Returns:
            NetworkDiagnosticsResult: Full diagnostic telemetry, verdict code, and fix recommendations.
        """
        nat_aligned = bool(
            self.dns["cached_public_ip"]
            and self.dns["cached_public_ip"] != "Detecting..."
            and self.dns["cached_public_ip"] != "Unknown"
            and self.dns["cached_dns_ip"] != "Unresolved"
            and self.dns["cached_public_ip"] == self.dns["cached_dns_ip"]
        )
        return await run_network_diagnostics_sweep(
            server_fps=server_fps,
            server_frame_time_ms=server_frame_time_ms,
            nat_aligned=nat_aligned,
            ping_runner=_execute_ping_probes,
        )
