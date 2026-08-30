"""Community Discovery Hub, Steam A2S UDP Probe, and Bare-Metal Telemetry Engine.

Provides multi-layer discovery tracking, Valve Steam A2S UDP packet queries,
Pocketpair Master Server directory probes, EOS Session ID journal scraping,
and bare-metal resource monitoring in accordance with Google Style Guide and 3 AM standards.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from .logger import log
from .types import (
    CombinedTelemetryPayload,
    DiscoveryHubPayload,
    HardwareTelemetryInfo,
    LogScraperInfo,
    NetworkMatrixInfo,
    PlayerLedgerMatrix,
    PlayerRecord,
    PocketpairMasterInfo,
    SecurityMatrixInfo,
    SteamA2SInfo,
    TopBadgeInfo,
)


def _resolve_default_ledger_path() -> Path:
    """Returns a writable ledger path, falling back to home dir if unprivileged."""
    if os.name == "nt":
        return Path.home() / ".palmanager" / "players.json"
    var_lib = Path("/var/lib/palmanager/players.json")
    try:
        var_lib.parent.mkdir(parents=True, exist_ok=True)
        return var_lib
    except PermissionError as err:
        log.debug("Permission denied creating /var/lib/palmanager (%s), using home dir fallback.", err)
        return Path.home() / ".palmanager" / "players.json"
    except OSError as err:
        log.debug("OS error creating /var/lib/palmanager (%s), using home dir fallback.", err)
        return Path.home() / ".palmanager" / "players.json"


def _resolve_default_session_path() -> Path:
    """Returns a writable session cache path, falling back to home dir if unprivileged."""
    if os.name == "nt":
        return Path.home() / ".palmanager" / "eos_session.json"
    var_lib = Path("/var/lib/palmanager/eos_session.json")
    try:
        var_lib.parent.mkdir(parents=True, exist_ok=True)
        return var_lib
    except PermissionError as err:
        log.debug("Permission denied creating /var/lib/palmanager (%s), using home dir fallback.", err)
        return Path.home() / ".palmanager" / "eos_session.json"
    except OSError as err:
        log.debug("OS error creating /var/lib/palmanager (%s), using home dir fallback.", err)
        return Path.home() / ".palmanager" / "eos_session.json"


DEFAULT_LEDGER_PATH: Path = _resolve_default_ledger_path()
DEFAULT_SESSION_PATH: Path = _resolve_default_session_path()


def resolve_host_lan_ip() -> str:
    """Discovers the active host primary LAN IP address (e.g. eth0 / 192.168.x.x)."""
    env_ip = os.getenv("PALWORLD_HOST_IP") or os.getenv("HOST_IP")
    if env_ip and env_ip != "127.0.0.1":
        return env_ip

    # 1. Socket routing probe (connects to a public DNS IP without transmitting packets)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            detected_ip = s.getsockname()[0]
            if detected_ip and not detected_ip.startswith("127."):
                return detected_ip
    except OSError as err:
        log.debug("Socket routing LAN IP probe failed: %s", err)

    # 2. Network interfaces probe via psutil (eth0, eno1, enp*, wlan*, Ethernet)
    try:
        import psutil

        for iface_name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    if iface_name.startswith(("eth", "en", "wl", "bond", "Ethernet", "Wi-Fi")):
                        return addr.address
    except Exception as err:
        log.debug("Psutil network interface probe failed: %s", err)

    # 3. Hostname DNS resolution
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        if host_ip and not host_ip.startswith("127."):
            return host_ip
    except Exception as err:
        log.debug("Hostname LAN IP resolution failed: %s", err)

    return "127.0.0.1"


class CommunityTracker:
    """Probes Palworld community listings, A2S UDP sockets, and bare-metal resource telemetry.

    Attributes:
        server_name (str): Configured dedicated server name.
        domain (str): Public DuckDNS domain name.
        player_ledger_path (Path): Filesystem path to the persistent player roster JSON file.
        log_registered (bool): True if an active EOS lobby registration was found in journal logs.
        log_first_seen (str | None): Timestamp string when EOS registration was first scraped.
        log_session_id (str | None): Active EOS lobby session identifier string.
        log_last_matched_line (str): Last matched journal line text.
        pocketpair_listed (bool): True if server is indexed in Pocketpair's public master directory.
        pocketpair_server_id (str): Unique Pocketpair directory server ID or 'Unlisted'.
        pocketpair_name (str): Server name indexed in Pocketpair's directory.
        pocketpair_version (str): Game build version reported by Pocketpair's directory.
        last_pocketpair_check (float): Epoch timestamp of last directory check.
        a2s_ping_ms (float | None): Measured round-trip latency in ms to Valve A2S UDP socket.
        a2s_responsive (bool): True if UDP socket responded to A2S_INFO packet query.
        a2s_server_name (str | None): Server name parsed from Valve A2S binary packet.
        a2s_map_name (str | None): Map name parsed from Valve A2S binary packet.
        a2s_players (int): Current player count from A2S query.
        a2s_max_players (int): Max player count from A2S query.
        cached_public_ip (str): Cached WAN public IP string.
        cached_dns_ip (str): Cached DuckDNS resolved IP string.
        last_ip_check (float): Epoch timestamp of last WAN/DNS probe.
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
        self.server_name: str = server_name or os.getenv("PALWORLD_SERVER_NAME") or "Palworld Dedicated Server"
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
        self.domain: str = clean_domain

        if player_ledger_path is not None:
            self.player_ledger_path: Path = Path(player_ledger_path)
        else:
            env_ledger = os.getenv("PLAYER_LEDGER_PATH")
            self.player_ledger_path = Path(env_ledger) if env_ledger else DEFAULT_LEDGER_PATH

        if session_cache_path is not None:
            self.session_cache_path: Path = Path(session_cache_path)
        else:
            env_session = os.getenv("SESSION_CACHE_PATH")
            self.session_cache_path = Path(env_session) if env_session else DEFAULT_SESSION_PATH

        # 1. Local Log Scraper State (EOS Public Session ID)
        self.log_registered: bool = False
        self.log_first_seen: str | None = None
        self.log_session_id: str | None = None
        self.log_last_matched_line: str = "Awaiting initial engine log lines..."

        # Load persisted session cache if available across web service restarts
        self._load_session_cache()

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
        self._last_auto_duckdns_sync: float = 0.0

        # 5. Persistent Player Ledger
        self.players_history: dict[str, PlayerRecord] = self._load_player_ledger()

    def _load_session_cache(self) -> None:
        """Loads cached EOS session metadata from disk across service restarts."""
        if not self.session_cache_path.exists():
            return
        try:
            content = self.session_cache_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                self.log_registered = bool(data.get("registered", False))
                self.log_session_id = data.get("session_id")
                self.log_first_seen = data.get("first_seen")
                if data.get("last_line"):
                    self.log_last_matched_line = str(data["last_line"])
        except FileNotFoundError as err:
            log.debug("Session cache file disappeared during read: %s", err)
        except PermissionError as err:
            log.debug("Permission denied reading session cache: %s", err)
        except json.JSONDecodeError as err:
            log.debug("Malformed JSON in session cache: %s", err)
        except OSError as err:
            log.debug("I/O error reading session cache: %s", err)

    def _save_session_cache(self) -> None:
        """Persists current EOS session state to disk using an atomic file swap."""
        try:
            self.session_cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "registered": self.log_registered,
                "session_id": self.log_session_id,
                "first_seen": self.log_first_seen,
                "last_line": self.log_last_matched_line,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
            temp_path = self.session_cache_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp_path.replace(self.session_cache_path)
        except PermissionError as err:
            log.debug("Permission denied writing session cache: %s", err)
        except OSError as err:
            log.debug("Failed writing session cache: %s", err)

    def _load_player_ledger(self) -> dict[str, PlayerRecord]:
        """Loads historical player roster from persistent JSON storage.

        Returns:
            dict[str, PlayerRecord]: Mapping of player IDs to player history records.
        """
        if not self.player_ledger_path.exists():
            return {}

        try:
            content = self.player_ledger_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                return data
        except FileNotFoundError as err:
            log.debug("Player ledger file disappeared during read: %s", err)
        except PermissionError as err:
            log.warning("Permission denied reading player ledger at %s: %s", self.player_ledger_path, err)
        except json.JSONDecodeError as err:
            log.warning("Malformed JSON in player ledger at %s: %s", self.player_ledger_path, err)
        except OSError as err:
            log.warning("I/O error reading player ledger at %s: %s", self.player_ledger_path, err)
        return {}

    def _save_player_ledger(self) -> None:
        """Persists current player roster to disk using an atomic file swap pattern."""
        try:
            self.player_ledger_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.player_ledger_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(self.players_history, indent=2), encoding="utf-8")
            temp_path.replace(self.player_ledger_path)
        except PermissionError as err:
            log.warning("Permission denied writing player ledger to %s: %s", self.player_ledger_path, err)
        except OSError as err:
            log.warning("Failed to write player ledger to %s: %s", self.player_ledger_path, err)

    @staticmethod
    def parse_a2s_packet(data: bytes, latency_ms: float | None = None) -> SteamA2SInfo:
        """Parses a Valve A2S_INFO binary UDP response packet.

        Extracts header byte (0x49), protocol version, and null-terminated strings.

        Args:
            data (bytes): Raw binary UDP payload received from game socket.
            latency_ms (float | None): Measured round-trip latency in milliseconds.

        Returns:
            SteamA2SInfo: Parsed packet fields including server name and map name.
        """
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
        return {
            "responsive": bool(data),
            "ping_ms": latency_ms,
            "server_name": "",
            "map_name": "",
            "folder": "",
            "game": "",
        }

    async def probe_steam_a2s_info(self, host: str = "127.0.0.1", port: int = 8211) -> SteamA2SInfo:
        """Direct UDP A2S_INFO packet query to the server socket (measures raw latency in ms).

        Args:
            host (str): IP address of the target Palworld server.
            port (int): UDP game port to query.

        Returns:
            SteamA2SInfo: Latency metrics and server name parsed from socket.
        """
        query_packet = b"\xff\xff\xff\xffTSource Engine Query\x00"
        loop = asyncio.get_running_loop()

        def _sync_a2s_query() -> SteamA2SInfo:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1.5)
            t_start = time.perf_counter()
            try:
                sock.sendto(query_packet, (host, port))
                data, _ = sock.recvfrom(2048)
                t_end = time.perf_counter()
                latency_ms = round((t_end - t_start) * 1000, 1)
                return self.parse_a2s_packet(data, latency_ms)
            except TimeoutError as err:
                log.debug("A2S socket query timed out on %s:%s: %s", host, port, err)
                return {
                    "responsive": False,
                    "ping_ms": None,
                    "server_name": "",
                    "map_name": "",
                    "folder": "",
                    "game": "",
                }
            except OSError as err:
                log.debug("A2S socket query failed on %s:%s: %s", host, port, err)
                return {
                    "responsive": False,
                    "ping_ms": None,
                    "server_name": "",
                    "map_name": "",
                    "folder": "",
                    "game": "",
                }
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
        except Exception as err:
            log.debug("A2S async executor exception: %s", err)
            self.a2s_responsive = False
            self.a2s_ping_ms = None
            return {
                "responsive": False,
                "ping_ms": None,
                "server_name": "",
                "map_name": "",
                "folder": "",
                "game": "",
            }

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
        except httpx.TimeoutException as err:
            log.debug("Pocketpair directory probe timed out: %s", err)
        except httpx.ConnectError as err:
            log.debug("Pocketpair directory connection failed: %s", err)
        except httpx.HTTPError as err:
            log.debug("Pocketpair directory probe HTTP error: %s", err)

        self.last_pocketpair_check = time.time()

    def probe_local_logs(self) -> LogScraperInfo:
        """Performs real-time and retroactive log scraping across active/archived Pal.log files and journalctl.

        Returns:
            LogScraperInfo: Scraped session information and status metadata.
        """
        candidate_log_dirs = [
            Path("/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Logs"),
            Path("/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/Logs"),
            Path("/home/steam/PalServer/Pal/Saved/Logs"),
        ]

        active_log_file: Path | None = None
        all_log_files: list[Path] = []
        for log_dir in candidate_log_dirs:
            if log_dir.is_dir():
                main_log = log_dir / "Pal.log"
                if main_log.exists():
                    active_log_file = main_log
                try:
                    found_logs = sorted(
                        log_dir.glob("Pal*.log"),
                        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
                        reverse=True,
                    )
                    all_log_files.extend(found_logs)
                except OSError as err:
                    log.debug("Error listing log directory %s: %s", log_dir, err)
                if all_log_files:
                    break

        # 1. Tail latest line from active Pal.log for real-time engine activity
        if active_log_file and active_log_file.exists():
            try:
                with active_log_file.open("r", encoding="utf-8", errors="ignore") as f:
                    recent_lines = f.readlines()[-50:]
                    for raw_line in reversed(recent_lines):
                        clean_line = raw_line.strip()
                        if clean_line and not clean_line.startswith("=") and len(clean_line) > 5:
                            self.log_last_matched_line = clean_line[:120]
                            break
            except OSError as err:
                log.debug("Error tailing active Pal.log: %s", err)

        # If already registered and have session ID, return cached info
        if self.log_registered and self.log_session_id:
            return {
                "registered": True,
                "session_id": self.log_session_id,
                "first_seen": self.log_first_seen,
                "last_line": self.log_last_matched_line,
                "status_label": "CONSOLE SEARCH READY",
                "status_color": "emerald",
                "crossplay_platforms": "(Steam, Xbox, PS5, Mac)",
            }

        # 2. Retroactive Deep Scan across all historical Pal*.log files
        pattern = (
            r"Created public lobby session|EOS-SDK.*sessions|"
            r"Lobby.*Registered|PublicSession|Steam server initialized|"
            r"Server registration succeeded|LogPalServer"
        )

        for log_file in all_log_files:
            try:
                with log_file.open("r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[:10000]
                for line in lines:
                    if re.search(pattern, line, re.IGNORECASE):
                        self.log_registered = True
                        match = re.search(r"SessionId:\s*([A-Za-z0-9_\-]+)", line)
                        if match:
                            self.log_session_id = match.group(1)

                        if not self.log_first_seen:
                            ts_match = re.search(r"\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2})", line)
                            if ts_match:
                                self.log_first_seen = ts_match.group(1).replace(".", "-").replace("-", " ", 1)
                            else:
                                self.log_first_seen = datetime.datetime.now(datetime.timezone.utc).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                        break
                if self.log_registered and self.log_session_id:
                    break
            except OSError as err:
                log.debug("Retroactive scan error on %s: %s", log_file, err)

        # 3. Retroactive Deep Scan in systemd journal (scans up to 5,000 lines from current boot)
        if not self.log_registered and os.name != "nt":
            try:
                sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"
                journalctl_bin = shutil.which("journalctl") or "/bin/journalctl"
                cmd = [sudo_bin, journalctl_bin, "-u", "palworld.service", "-b", "-n", "5000", "--no-pager"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
                if proc.returncode == 0:
                    lines = proc.stdout.splitlines()
                    for raw_line in reversed(lines[-50:]):
                        clean_line = raw_line.strip()
                        if clean_line and not clean_line.startswith("=") and len(clean_line) > 5:
                            self.log_last_matched_line = clean_line[:120]
                            break

                    for line in lines:
                        if re.search(pattern, line, re.IGNORECASE):
                            self.log_registered = True
                            match = re.search(r"SessionId:\s*([A-Za-z0-9_\-]+)", line)
                            if match:
                                self.log_session_id = match.group(1)

                            if not self.log_first_seen:
                                self.log_first_seen = datetime.datetime.now(datetime.timezone.utc).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            break
            except subprocess.TimeoutExpired as err:
                log.debug("Journalctl retroactive scan timed out: %s", err)
            except subprocess.SubprocessError as err:
                log.debug("Journalctl retroactive scan subprocess error: %s", err)
            except OSError as err:
                log.debug("Journalctl retroactive scan OS error: %s", err)

        if self.log_registered:
            self._save_session_cache()
            return {
                "registered": True,
                "session_id": self.log_session_id or "EOS-Session-Active",
                "first_seen": self.log_first_seen,
                "last_line": self.log_last_matched_line,
                "status_label": "CONSOLE SEARCH READY",
                "status_color": "emerald",
                "crossplay_platforms": "(Steam, Xbox, PS5, Mac)",
            }

        return {
            "registered": self.log_registered,
            "session_id": self.log_session_id,
            "first_seen": self.log_first_seen,
            "last_line": self.log_last_matched_line,
            "status_label": "CONSOLE SEARCH READY" if self.log_registered else "AWAITING EOS HANDSHAKE",
            "status_color": "emerald" if self.log_registered else "amber",
            "crossplay_platforms": "(Steam, Xbox, PS5, Mac)",
        }

    def probe_network_alignment(self) -> NetworkMatrixInfo:
        """Probes WAN public IP and DuckDNS resolution to verify network alignment.

        Returns:
            NetworkMatrixInfo: Public IP, resolved DNS IP, and alignment boolean status.
        """
        try:
            with httpx.Client(timeout=2.0) as client:
                res = client.get("https://api.ipify.org?format=json")
                if res.status_code == 200:
                    self.cached_public_ip = res.json().get("ip", "Unknown")
        except httpx.TimeoutException as err:
            log.debug("WAN IP detection timed out: %s", err)
        except httpx.ConnectError as err:
            log.debug("WAN IP connection failed: %s", err)
        except httpx.HTTPError as err:
            log.debug("WAN IP HTTP error: %s", err)

        try:
            if self.domain:
                self.cached_dns_ip = socket.gethostbyname(str(self.domain))
            else:
                self.cached_dns_ip = "Unresolved"
        except socket.gaierror as err:
            log.debug("DNS address resolution failed for %s: %s", self.domain, err)
            self.cached_dns_ip = "Unresolved"
        except OSError as err:
            log.debug("Socket OS error resolving %s: %s", self.domain, err)
            self.cached_dns_ip = "Unresolved"

        is_aligned = bool(
            self.cached_public_ip
            and self.cached_public_ip != "Detecting..."
            and self.cached_public_ip != "Unknown"
            and self.cached_dns_ip != "Unresolved"
            and self.cached_public_ip == self.cached_dns_ip
        )

        if not is_aligned and self.domain and "yourdomain" not in self.domain:
            now = time.time()
            if now - self._last_auto_duckdns_sync > 300.0:
                self._last_auto_duckdns_sync = now
                duck_script = Path("/home/steam/duckdns/duck.sh")
                if duck_script.exists():
                    try:
                        subprocess.Popen(
                            ["/bin/bash", str(duck_script)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        log.info("Triggered background DuckDNS sync to heal WAN/DNS IP mismatch.")
                    except OSError as err:
                        log.debug("Failed to spawn background DuckDNS sync: %s", err)

        lan_ip = resolve_host_lan_ip()
        public_port = 8211
        direct_host = self.domain if (self.domain and "yourdomain" not in self.domain) else self.cached_public_ip

        return {
            "public_ip": self.cached_public_ip or "Unknown",
            "dns_ip": self.cached_dns_ip or "Unresolved",
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
            PlayerLedgerMatrix: Active players, offline history, and roster totals.
        """
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        active_list: list[PlayerRecord] = []
        active_ids: set[str] = set()

        for p in live_players_raw:
            pid = str(p.get("playerId", ""))
            if not pid:
                continue
            active_ids.add(pid)

            player_record: PlayerRecord = {
                "playerId": pid,
                "userId": str(p.get("userId", "")),
                "name": str(p.get("name", "Unknown Tamer")),
                "level": int(p.get("level", 1)),
                "ping": float(p.get("ping", 0.0)),
                "location": {
                    "x": float(p.get("location_x", 0.0)),
                    "y": float(p.get("location_y", 0.0)),
                },
                "status": "ONLINE",
                "last_seen": now_str,
            }

            self.players_history[pid] = player_record
            active_list.append(player_record)

        offline_list: list[PlayerRecord] = []
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

    def get_hardware_telemetry(self) -> HardwareTelemetryInfo:
        """Collects bare-metal hardware and systemd cgroup 14GB allocation metrics.

        Returns:
            HardwareTelemetryInfo: System RAM, cgroup usage, swap, CPU, disk, and network stats.
        """
        cgroup_ram_bytes = 0
        cgroup_path = Path("/sys/fs/cgroup/system.slice/palworld.service/memory.current")
        if os.name != "nt" and cgroup_path.exists():
            try:
                content = cgroup_path.read_text(encoding="utf-8").strip()
                cgroup_ram_bytes = int(content)
            except FileNotFoundError as err:
                log.debug("Cgroup memory file not found at %s: %s", cgroup_path, err)
            except PermissionError as err:
                log.debug("Permission denied reading %s: %s", cgroup_path, err)
            except ValueError as err:
                log.debug("Invalid integer in %s: %s", cgroup_path, err)
            except OSError as err:
                log.debug("OS error reading %s: %s", cgroup_path, err)

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
        except OSError as err:
            log.debug("Disk usage telemetry failed for %s: %s", disk_path, err)
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
    ) -> CombinedTelemetryPayload:
        """Assembles full 3-section telemetry matrix for the reactive UI dashboard.

        Args:
            is_multiplay (bool): Whether multiplayer join mode is active.
            host_ip (str | None): Host LAN IP address.
            public_port (int): UDP game port (default: 8211).
            server_password (str): Current plaintext join password.
            rcon_port (int): Admin RCON port (default: 25575).
            rest_port (int): Internal REST port (default: 8212).
            max_players (int): Maximum server slot capacity.
            current_players (int): Count of connected tamers.

        Returns:
            CombinedTelemetryPayload: Complete telemetry schema consumed by dashboard UI.
        """
        self.probe_network_alignment()
        await self.probe_pocketpair_master_list()
        await self.probe_steam_a2s_info(host=host_ip or "127.0.0.1", port=public_port)
        log_res = self.probe_local_logs()

        lan_ip: str = host_ip if (host_ip and host_ip != "127.0.0.1") else resolve_host_lan_ip()

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
            badge_label = f"Direct Connect ({direct_connect_host}:{public_port})"
            badge_style = "bg-amber-900/60 border border-amber-500/50 text-amber-300"
            badge_dot = "bg-amber-400"

        top_badge: TopBadgeInfo = {
            "label": badge_label,
            "style": badge_style,
            "dot": badge_dot,
        }

        pocketpair_master: PocketpairMasterInfo = {
            "listed": self.pocketpair_listed,
            "server_id": self.pocketpair_server_id,
            "name": self.pocketpair_name,
            "version": self.pocketpair_version,
            "status_label": "OFFICIALLY LISTED" if self.pocketpair_listed else "COMMUNITY / DIRECT MODE",
            "status_color": "emerald" if self.pocketpair_listed else "amber",
        }

        network_matrix: NetworkMatrixInfo = {
            "public_ip": self.cached_public_ip,
            "dns_ip": self.cached_dns_ip,
            "domain": self.domain,
            "is_aligned": bool(self.cached_public_ip == self.cached_dns_ip),
            "lan_ip": lan_ip,
            "public_port": public_port,
            "direct_connect_addr": f"{direct_connect_host}:{public_port}",
            "lan_connect_addr": f"{lan_ip}:{public_port}",
        }

        discovery_hub: DiscoveryHubPayload = {
            "log_scraper": log_res,
            "pocketpair_master": pocketpair_master,
            "network_matrix": network_matrix,
        }

        a2s_telemetry: SteamA2SInfo = {
            "responsive": self.a2s_responsive,
            "ping_ms": self.a2s_ping_ms,
            "server_name": self.a2s_server_name or self.server_name,
            "map_name": self.a2s_map_name or "Pal/Maps/World",
            "folder": "Pal",
            "game": "Palworld",
        }

        security_matrix: SecurityMatrixInfo = {
            "is_password_protected": bool(server_password),
            "password_status_label": "🔒 Password Protected" if server_password else "🔓 Public Access (No Password)",
            "server_password": server_password,
            "rcon_port": rcon_port,
            "rest_port": rest_port,
            "max_players": max_players,
            "current_players": current_players,
            "slot_capacity_label": f"{current_players} / {max_players} Tamers",
        }

        return {
            "top_badge": top_badge,
            "discovery_hub": discovery_hub,
            "a2s_telemetry": a2s_telemetry,
            "security_matrix": security_matrix,
        }
