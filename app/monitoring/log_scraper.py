"""Real-time and retroactive Palworld log scraper and console journal reader.

Extracts EOS lobby sessions from active Pal.log files and systemd journalctl,
maintaining an atomic persistent session cache and sanitizing output for web console display.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from app.core.config import is_posix
from app.core.logger import log, sanitize_log_text
from app.core.types import LogScraperInfo

DEFAULT_SESSION_PATH: Path = (
    Path("/var/lib/palmanager/eos_session_cache.json")
    if os.name != "nt"
    else Path.home() / ".palmanager" / "eos_session_cache.json"
)

LOG_SEARCH_PATTERN: str = (
    r"Created public lobby session|EOS-SDK.*sessions|"
    r"Lobby.*Registered|PublicSession|Steam server initialized|"
    r"Server registration succeeded|LogPalServer"
)


class PalLogScraper:
    """Extracts session IDs and tails live engine logs for console and discovery hub display.

    Attributes:
        session_cache_path (Path): Filepath to persistent session cache JSON.
        registered (bool): True if an active EOS lobby registration was found.
        first_seen (str | None): Timestamp string when EOS registration was first logged.
        session_id (str | None): Active EOS lobby session identifier string.
        last_matched_line (str): Last matched log or journal line text.
    """

    def __init__(self, session_cache_path: str | Path | None = None) -> None:
        """Initializes the PalLogScraper.

        Args:
            session_cache_path (str | Path | None): Optional custom session cache file path.
        """
        if session_cache_path is not None:
            self.session_cache_path: Path = Path(session_cache_path)
        else:
            env_session = os.getenv("SESSION_CACHE_PATH")
            self.session_cache_path = Path(env_session) if env_session else DEFAULT_SESSION_PATH

        self.registered: bool = False
        self.first_seen: str | None = None
        self.session_id: str | None = None
        self.last_matched_line: str = "Awaiting initial engine log lines..."

        self._load_session_cache()

    def _load_session_cache(self) -> None:
        """Loads cached EOS session information from disk if available."""
        if not self.session_cache_path.is_file():
            return
        try:
            raw_text = self.session_cache_path.read_text(encoding="utf-8")
            cache_data = json.loads(raw_text)
            cached_session_id = cache_data.get("session_id")
            if cached_session_id:
                self.session_id = str(cached_session_id)
                self.registered = True
                self.first_seen = cache_data.get("first_seen")
                self.last_matched_line = cache_data.get("last_line", self.last_matched_line)
                log.info("Restored EOS Session ID (%s) from cache (%s)", self.session_id, self.session_cache_path)
        except PermissionError as err:
            log.warning("Permission denied loading session cache from %s: %s", self.session_cache_path, err)
        except json.JSONDecodeError as err:
            log.warning("Corrupted JSON in session cache file %s: %s", self.session_cache_path, err)
        except OSError as err:
            log.warning("OS error loading session cache from %s: %s", self.session_cache_path, err)

    def _save_session_cache(self) -> None:
        """Persists discovered EOS session info atomically to disk."""
        if not self.session_id:
            return
        cache_data = {
            "session_id": self.session_id,
            "first_seen": self.first_seen,
            "last_line": self.last_matched_line,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        try:
            self.session_cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.session_cache_path.with_suffix(".tmp")
            temp_file.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
            temp_file.replace(self.session_cache_path)
            log.debug("Persisted EOS Session ID (%s) to cache (%s)", self.session_id, self.session_cache_path)
        except PermissionError as err:
            log.warning("Permission denied writing session cache to %s: %s", self.session_cache_path, err)
        except OSError as err:
            log.warning("OS error writing session cache to %s: %s", self.session_cache_path, err)

    @staticmethod
    def _find_log_files() -> tuple[Path | None, list[Path]]:
        """Scans standard PalServer log directories for active and archived log files."""
        candidate_log_dirs = [
            Path("/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Logs"),
            Path("/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/Logs"),
            Path("/home/steam/PalServer/Pal/Saved/Logs"),
        ]
        active_log_file: Path | None = None
        all_log_files: list[Path] = []
        for log_dir in candidate_log_dirs:
            if not log_dir.is_dir():
                continue
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
        return active_log_file, all_log_files

    def _tail_active_log(self, active_log_file: Path | None) -> None:
        """Tails the latest line from active Pal.log for real-time engine activity."""
        if not active_log_file or not active_log_file.exists():
            return
        try:
            with active_log_file.open("r", encoding="utf-8", errors="ignore") as f:
                recent_lines = f.readlines()[-50:]
                for raw_line in reversed(recent_lines):
                    clean_line = raw_line.strip()
                    if clean_line and not clean_line.startswith("=") and len(clean_line) > 5:
                        self.last_matched_line = clean_line[:120]
                        break
        except OSError as err:
            log.debug("Error tailing active Pal.log: %s", err)

    def _extract_session_from_lines(self, lines: list[str]) -> bool:
        """Parses lines for EOS session registration identifiers and timestamps."""
        for line in lines:
            if not re.search(LOG_SEARCH_PATTERN, line, re.IGNORECASE):
                continue
            self.registered = True
            match = re.search(r"SessionId:\s*([A-Za-z0-9_\-]+)", line)
            if match:
                self.session_id = match.group(1)

            if not self.first_seen:
                ts_match = re.search(r"\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2})", line)
                if ts_match:
                    self.first_seen = ts_match.group(1).replace(".", "-").replace("-", " ", 1)
                else:
                    self.first_seen = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return True
        return False

    def _scan_historical_log_files(self, all_log_files: list[Path]) -> None:
        """Scans historical Pal*.log files for session registration information."""
        for log_file in all_log_files:
            try:
                with log_file.open("r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[:10000]
                if self._extract_session_from_lines(lines) and self.session_id:
                    break
            except OSError as err:
                log.debug("Retroactive scan error on %s: %s", log_file, err)

    def _scan_journalctl(self, is_posix_checker: Any = None) -> None:
        """Performs retroactive deep scan in systemd journal on POSIX hosts."""
        checker = is_posix_checker or is_posix
        if self.registered or not checker():
            return
        try:
            sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"
            journalctl_bin = shutil.which("journalctl") or "/bin/journalctl"
            cmd = [sudo_bin, journalctl_bin, "-u", "palworld.service", "-b", "-n", "5000", "--no-pager"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)  # nosec B603
            if proc.returncode != 0:
                return

            lines = proc.stdout.splitlines()
            for raw_line in reversed(lines[-50:]):
                clean_line = raw_line.strip()
                if clean_line and not clean_line.startswith("=") and len(clean_line) > 5:
                    self.last_matched_line = clean_line[:120]
                    break
            self._extract_session_from_lines(lines)
        except subprocess.TimeoutExpired as err:
            log.debug("Journalctl retroactive scan timed out: %s", err)
        except subprocess.SubprocessError as err:
            log.debug("Journalctl retroactive scan subprocess error: %s", err)
        except OSError as err:
            log.debug("Journalctl retroactive scan OS error: %s", err)

    def probe_local_logs(self, is_posix_checker: Any = None) -> LogScraperInfo:
        """Performs real-time and retroactive log scraping across Pal.log files and journalctl.

        Args:
            is_posix_checker (Any): Optional posix platform check callable.

        Returns:
            LogScraperInfo: Scraped session information and status metadata.
        """
        active_log_file, all_log_files = self._find_log_files()
        self._tail_active_log(active_log_file)

        if self.registered and self.session_id:
            return {
                "registered": True,
                "session_id": self.session_id,
                "first_seen": self.first_seen,
                "last_line": self.last_matched_line,
                "status_label": "CONSOLE SEARCH READY",
                "status_color": "emerald",
                "crossplay_platforms": "(Steam, Xbox, PS5, Mac)",
            }

        self._scan_historical_log_files(all_log_files)
        self._scan_journalctl(is_posix_checker=is_posix_checker)

        if self.registered:
            self._save_session_cache()

        return {
            "registered": self.registered,
            "session_id": self.session_id or ("EOS-Session-Active" if self.registered else None),
            "first_seen": self.first_seen,
            "last_line": self.last_matched_line,
            "status_label": "CONSOLE SEARCH READY" if self.registered else "AWAITING EOS HANDSHAKE",
            "status_color": "emerald" if self.registered else "amber",
            "crossplay_platforms": "(Steam, Xbox, PS5, Mac)",
        }

    def read_server_logs(
        self,
        tail: int = 200,
        filter_query: str | None = None,
        level: str = "ALL",
    ) -> dict[str, Any]:
        """Reads, filters, and sanitizes recent engine log lines for the live console.

        Args:
            tail (int): Number of lines to return (bounded between 10 and 2000).
            filter_query (str | None): Optional text or regex filter.
            level (str): Log level category filter ('ALL', 'ENGINE', 'EOS', 'WARN_ERROR').

        Returns:
            dict[str, Any]: Log metadata and sanitized lines list.
        """
        tail = max(10, min(tail, 2000))
        candidate_log_files = [
            Path("/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Logs/Pal.log"),
            Path("/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/Logs/Pal.log"),
            Path("/home/steam/PalServer/Pal/Saved/Logs/Pal.log"),
        ]

        source_path = "journalctl"
        raw_lines: list[str] = []

        for log_file in candidate_log_files:
            if not log_file.exists():
                continue
            try:
                with log_file.open("r", encoding="utf-8", errors="ignore") as f:
                    raw_lines = f.readlines()[-tail * 3:]
                source_path = str(log_file)
                break
            except OSError as err:
                log.debug("Error reading log file %s: %s", log_file, err)

        if not raw_lines and is_posix():
            raw_lines = self._read_journal_tail(tail)

        filtered = self._filter_log_lines(raw_lines, filter_query, level)
        return {
            "status": "success",
            "source": source_path,
            "total_lines_scanned": len(raw_lines),
            "returned_lines": len(filtered[-tail:]),
            "lines": filtered[-tail:],
        }

    @staticmethod
    def _read_journal_tail(tail: int) -> list[str]:
        """Reads recent log lines from journalctl if log files are inaccessible."""
        try:
            sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"
            journalctl_bin = shutil.which("journalctl") or "/bin/journalctl"
            cmd = [sudo_bin, journalctl_bin, "-u", "palworld.service", "-n", str(tail * 3), "--no-pager"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)  # nosec B603
            if proc.returncode == 0:
                return proc.stdout.splitlines()
        except subprocess.SubprocessError as err:
            log.debug("Journalctl log query subprocess error: %s", err)
        except OSError as err:
            log.debug("Journalctl log query OS error: %s", err)
        return []

    @staticmethod
    def _filter_log_lines(raw_lines: list[str], filter_query: str | None, level: str) -> list[str]:
        """Applies level and query filters and sanitizes matching lines."""
        filtered: list[str] = []
        level_lower = level.lower()

        for line in raw_lines:
            clean = line.strip("\r\n")
            if not clean:
                continue

            if level_lower == "engine" and "logpalserver" not in clean.lower():
                continue
            if level_lower == "eos" and not re.search(r"eos|session|lobby|steam", clean, re.IGNORECASE):
                continue
            if level_lower == "warn_error" and not re.search(r"warning|error|fatal|crash|fail", clean, re.IGNORECASE):
                continue

            if filter_query:
                try:
                    if not re.search(filter_query, clean, re.IGNORECASE):
                        continue
                except re.error as err:
                    log.debug("Regex search failed for '%s', falling back to substring: %s", filter_query, err)
                    if filter_query.lower() not in clean.lower():
                        continue

            filtered.append(sanitize_log_text(clean))
        return filtered
