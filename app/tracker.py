import asyncio
import datetime
import json
import os
import re
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional
import httpx
import psutil

DUCKDNS_LOG = os.getenv(
    "DUCKDNS_LOG_PATH",
    "/home/steam/duckdns/duck.log" if os.name != "nt" else os.path.expanduser("~/.palmanager/duck.log"),
)
PLAYER_LEDGER = os.getenv(
    "PLAYER_LEDGER_PATH",
    "/var/lib/palmanager/players.json" if os.name != "nt" else os.path.expanduser("~/.palmanager/players.json"),
)


class CommunityTracker:
    def __init__(
        self,
        server_name: Optional[str] = None,
        domain: Optional[str] = None,
    ):
        self.server_name = server_name or os.getenv("PALWORLD_SERVER_NAME", "Palworld Dedicated Server")
        self.domain = domain or os.getenv("PALWORLD_SERVER_DOMAIN", "yourdomain.duckdns.org")
        self.battlemetrics_indexed: bool = False
        self.battlemetrics_first_seen: Optional[str] = None
        self.battlemetrics_id: Optional[str] = None

        self.log_registered: bool = False
        self.log_first_seen: Optional[str] = None
        self.last_check_time: Optional[str] = None

        self.next_bm_allowed_time: float = 0.0
        self.consecutive_429_count: int = 0
        self.last_bm_status: str = "SCANNING"

        self.cached_public_ip: str = "Detecting..."
        self.cached_dns_ip: str = "Resolving..."
        self.last_ip_check: float = 0.0

        self.players_history: Dict[str, Any] = self._load_player_ledger()

    def _load_player_ledger(self) -> Dict[str, Any]:
        if os.path.exists(PLAYER_LEDGER):
            try:
                with open(PLAYER_LEDGER, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_player_ledger(self):
        try:
            os.makedirs(os.path.dirname(PLAYER_LEDGER), exist_ok=True)
            with open(PLAYER_LEDGER, "w", encoding="utf-8") as f:
                json.dump(self.players_history, f, indent=2)
        except Exception:
            pass

    async def probe_battlemetrics_loop(self):
        while True:
            try:
                now = time.time()
                if now >= self.next_bm_allowed_time:
                    url = f"https://api.battlemetrics.com/servers?filter[game]=palworld&filter[search]={self.server_name}"
                    async with httpx.AsyncClient(timeout=6.0) as client:
                        res = await client.get(url, headers={"User-Agent": "Palworld-Manager-Daemon/1.0"})

                        if res.status_code == 200:
                            self.consecutive_429_count = 0
                            self.last_bm_status = "OK"
                            data = res.json()
                            servers = data.get("data", [])
                            if servers:
                                s = servers[0]
                                self.battlemetrics_indexed = True
                                if not self.battlemetrics_first_seen:
                                    self.battlemetrics_first_seen = datetime.datetime.now().strftime(
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                                self.battlemetrics_id = s.get("id")
                            self.next_bm_allowed_time = time.time() + 60.0

                        elif res.status_code == 429:
                            self.consecutive_429_count += 1
                            self.last_bm_status = "THROTTLED"
                            retry_after = res.headers.get("Retry-After")
                            wait_seconds = (
                                int(retry_after)
                                if (retry_after and retry_after.isdigit())
                                else min(30 * (2 ** (self.consecutive_429_count - 1)), 300)
                            )
                            self.next_bm_allowed_time = time.time() + wait_seconds
                        else:
                            self.next_bm_allowed_time = time.time() + 60.0
            except Exception:
                self.next_bm_allowed_time = time.time() + 60.0

            await asyncio.sleep(2)

    def probe_local_logs(self) -> Dict[str, Any]:
        if self.log_registered:
            return {"registered": True, "first_seen": self.log_first_seen}

        if os.name != "nt":
            try:
                cmd = ["sudo", "journalctl", "-u", "palworld.service", "-n", "300", "--no-pager"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        if re.search(
                            r"Created public lobby session|EOS-SDK.*sessions|Lobby.*Registered|PublicSession",
                            line,
                            re.IGNORECASE,
                        ):
                            self.log_registered = True
                            if not self.log_first_seen:
                                self.log_first_seen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            return {"registered": True, "first_seen": self.log_first_seen}
            except Exception:
                pass
        return {"registered": False, "first_seen": self.log_first_seen}

    async def probe_ip_and_dns(self):
        if time.time() - self.last_ip_check < 120.0 and self.cached_public_ip != "Detecting...":
            return

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get("https://api.ipify.org?format=json")
                if res.status_code == 200:
                    self.cached_public_ip = res.json().get("ip", "Unknown")
        except Exception:
            pass

        try:
            self.cached_dns_ip = socket.gethostbyname(self.domain)
        except Exception:
            self.cached_dns_ip = "Unresolved"

        self.last_ip_check = time.time()

    def update_and_get_players(self, live_players_raw: List[Dict[str, Any]]) -> Dict[str, Any]:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        active_list = []
        active_ids = set()

        for p in live_players_raw:
            pid = str(p.get("playerId") or p.get("userId") or p.get("name"))
            active_ids.add(pid)
            name = p.get("name", "Unknown Pal Tamer")
            lvl = p.get("level", 1)
            ping = round(float(p.get("ping", 0)), 1)
            loc_x = round(float(p.get("location_x", 0)), 1)
            loc_y = round(float(p.get("location_y", 0)), 1)

            entry = {
                "playerId": pid,
                "userId": p.get("userId", pid),
                "name": name,
                "level": lvl,
                "ping": ping,
                "coords": f"X: {loc_x}, Y: {loc_y}",
                "status": "ONLINE",
                "last_seen": now_str,
            }
            active_list.append(entry)
            self.players_history[pid] = entry

        self._save_player_ledger()

        offline_list = []
        for pid, data in self.players_history.items():
            if pid not in active_ids:
                d = dict(data)
                d["status"] = "OFFLINE"
                offline_list.append(d)

        return {
            "active_count": len(active_list),
            "total_registered": len(self.players_history),
            "active_players": active_list,
            "offline_players": offline_list,
        }

    def get_hardware_telemetry(self) -> Dict[str, Any]:
        cgroup_ram_bytes = 0
        cgroup_path = "/sys/fs/cgroup/system.slice/palworld.service/memory.current"
        if os.path.exists(cgroup_path):
            try:
                with open(cgroup_path, "r") as f:
                    cgroup_ram_bytes = int(f.read().strip())
            except Exception:
                pass

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        cpu_pcts = psutil.cpu_percent(percpu=True)
        net_all = psutil.net_io_counters(pernic=True)
        net = net_all.get("eth0", psutil.net_io_counters())

        return {
            "cgroup_used_gb": round(cgroup_ram_bytes / (1024**3), 2),
            "cgroup_limit_gb": 14.0,
            "cgroup_pct": round((cgroup_ram_bytes / (14 * 1024**3)) * 100, 1) if cgroup_ram_bytes else 0,
            "host_ram_used_gb": round((vm.total - vm.available) / (1024**3), 2),
            "host_ram_total_gb": round(vm.total / (1024**3), 2),
            "host_ram_pct": vm.percent,
            "host_swap_used_gb": round(swap.used / (1024**3), 2),
            "host_swap_total_gb": round(swap.total / (1024**3), 2),
            "cpu_cores": cpu_pcts,
            "cpu_avg": round(sum(cpu_pcts) / len(cpu_pcts), 1) if cpu_pcts else 0,
            "net_bytes_sent_mb": round(net.bytes_sent / (1024**2), 1),
            "net_bytes_recv_mb": round(net.bytes_recv / (1024**2), 1),
        }

    async def get_combined_telemetry(
        self, is_multiplay: bool = True, host_ip: Optional[str] = None
    ) -> Dict[str, Any]:
        self.last_check_time = datetime.datetime.now().strftime("%H:%M:%S")
        await self.probe_ip_and_dns()
        log_res = self.probe_local_logs()

        lan_ip = host_ip or os.getenv("PALWORLD_HOST_IP", "127.0.0.1")
        now = time.time()
        backoff_sec = max(0, int(self.next_bm_allowed_time - now)) if self.next_bm_allowed_time > now else 0
        bm_status_label = (
            "INDEXED"
            if self.battlemetrics_indexed
            else ("THROTTLED" if self.last_bm_status == "THROTTLED" else "SCANNING")
        )

        dns_match = (
            self.cached_public_ip != "Detecting..."
            and self.cached_public_ip != "Unknown"
            and self.cached_public_ip == self.cached_dns_ip
        )

        if self.battlemetrics_indexed:
            badge_label = "Community Listed"
            badge_style = "bg-emerald-900/60 border border-emerald-500/50 text-emerald-300 font-bold"
            badge_dot = "bg-emerald-400"
        elif not is_multiplay:
            badge_label = "Private Server"
            badge_style = "bg-slate-800 border border-slate-700 text-slate-300"
            badge_dot = "bg-slate-400"
        else:
            badge_label = f"Direct Connect Ready ({lan_ip}:8211)"
            badge_style = "bg-amber-900/60 border border-amber-500/50 text-amber-300"
            badge_dot = "bg-amber-400 animate-pulse"

        return {
            "top_badge": {
                "label": badge_label,
                "style": badge_style,
                "dot": badge_dot,
                "is_indexed": self.battlemetrics_indexed,
            },
            "network_matrix": {
                "public_wan_ip": self.cached_public_ip,
                "duckdns_a_record": self.cached_dns_ip,
                "dns_aligned": dns_match,
                "lan_ip": lan_ip,
            },
            "source_of_truth_battlemetrics": {
                "status": bm_status_label,
                "indexed": self.battlemetrics_indexed,
                "first_seen": self.battlemetrics_first_seen,
                "server_id": self.battlemetrics_id,
                "backoff_remaining_sec": backoff_sec,
            },
            "backup_log_scraper": {
                "status": "HEARTBEAT_DETECTED" if log_res.get("registered") else "AWAITING_BEAT",
                "registered": log_res.get("registered", False),
                "first_seen": self.log_first_seen,
            },
            "last_probed": self.last_check_time,
        }
