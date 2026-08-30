"""Centralized structured logging with sensitive credential redaction, rotation, and Discord mirroring.

Provides high-performance rotating file logs, console stream formatting, and non-blocking
Discord webhook mirroring with zero-leak redaction of administrative tokens, passwords, and secrets.
"""

from __future__ import annotations

import datetime
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts sensitive credentials, passwords, and tokens.

    Scans log message text using regular expressions to sanitize administrative secrets
    prior to emission to console streams, disk files, or Discord webhooks.
    """

    PATTERNS: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r'(?i)(admin_password|password|token|secret)\s*[:=]\s*["\']?([^"\'\s,]+)'), r"\1=[REDACTED]"),
        (re.compile(r"(?i)(Basic|Bearer)\s+[A-Za-z0-9+/=._-]+"), r"\1 [REDACTED]"),
        (
            re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"),
            r"[REDACTED_UUID]",
        ),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Filters and sanitizes the log record message string.

        Args:
            record (logging.LogRecord): The log record being processed.

        Returns:
            bool: Always True to allow emission of sanitized record.
        """
        if isinstance(record.msg, str):
            msg = record.msg
            for pattern, replacement in self.PATTERNS:
                msg = pattern.sub(replacement, msg)
            record.msg = msg
        return True


class DiscordLogHandler(logging.Handler):
    """Asynchronous worker-queued logging handler that mirrors logs to Discord via webhooks.

    Dispatches formatted embeds with visual urgency indicators and tags @thestygianarchitect
    for CRITICAL alerts.
    """

    COLOR_CRITICAL: int = 0x990000  # Deep Crimson
    COLOR_ERROR: int = 0xEF4444  # Red
    COLOR_WARNING: int = 0xF59E0B  # Amber
    COLOR_INFO: int = 0x3B82F6  # Blue

    def __init__(
        self,
        webhook_url: str,
        level: int = logging.ERROR,
        critical_ping: str = "@thestygianarchitect",
    ) -> None:
        """Initializes the Discord logging handler with background worker queue.

        Args:
            webhook_url (str): Target Discord incoming webhook URL.
            level (int): Minimum log level threshold for mirroring (default: ERROR).
            critical_ping (str): User or role tag for CRITICAL events (default: '@thestygianarchitect').
        """
        super().__init__(level=level)
        self.webhook_url = webhook_url
        self.critical_ping = critical_ping
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=200)
        self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="DiscordLogWorker")
        self._worker_thread.start()

    def _worker(self) -> None:
        """Background thread consumer that dispatches queued log payloads to Discord with 429 backoff."""
        while True:
            try:
                payload = self._queue.get()
                if payload is None:
                    break
                with httpx.Client(timeout=4.0) as client:
                    res = client.post(self.webhook_url, json=payload)
                    if res.status_code == 429:
                        try:
                            retry_data = res.json()
                            retry_after = float(retry_data.get("retry_after", 1.5))
                        except (ValueError, KeyError) as err:
                            sys.stderr.write(f"Discord retry_after parse error: {err}\n")
                            retry_after = 2.0
                        sys.stderr.write(f"Discord rate limit (HTTP 429) hit. Backing off for {retry_after:.1f}s...\n")
                        time.sleep(retry_after)
                        client.post(self.webhook_url, json=payload)
            except Exception as err:
                sys.stderr.write(f"DiscordLogHandler worker dispatch error: {err}\n")
            finally:
                self._queue.task_done()

    def emit(self, record: logging.LogRecord) -> None:
        """Enqueues a formatted Discord embed for log records meeting the level threshold.

        Args:
            record (logging.LogRecord): The log record to format and dispatch.
        """
        if not self.webhook_url or not self.webhook_url.startswith("https://discord.com/api/webhooks/"):
            return

        try:
            msg = self.format(record)
            level_name = record.levelname.upper()

            # Determine embed styling and urgency
            if record.levelno >= logging.CRITICAL:
                color = self.COLOR_CRITICAL
                title = f"🚨🚨 [CRITICAL ALERT] {record.name}"
                content = self.critical_ping
            elif record.levelno >= logging.ERROR:
                color = self.COLOR_ERROR
                title = f"🚨 [ERROR] {record.name}"
                content = ""
            elif record.levelno >= logging.WARNING:
                color = self.COLOR_WARNING
                title = f"⚠️ [WARNING] {record.name}"
                content = ""
            else:
                color = self.COLOR_INFO
                title = f"ℹ️ [{level_name}] {record.name}"
                content = ""

            fields = [
                {"name": "Location", "value": f"`{record.filename}:{record.lineno}`", "inline": True},
                {"name": "Severity", "value": f"`{level_name}`", "inline": True},
            ]

            # Format exception traceback if present
            if record.exc_info and record.exc_info[1]:
                tb_lines = traceback.format_exception(*record.exc_info)
                tb_str = "".join(tb_lines)[-800:]  # Limit to Discord field bounds
                fields.append({"name": "Traceback", "value": f"```python\n{tb_str}\n```", "inline": False})

            payload: dict[str, Any] = {
                "content": content if content else None,
                "embeds": [
                    {
                        "title": title,
                        "description": f"```{msg}```" if len(msg) < 1000 else f"```{msg[:990]}...```",
                        "color": color,
                        "fields": fields,
                        "footer": {"text": "Palworld Operations Suite Logging Mirror"},
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                ],
            }

            try:
                self._queue.put_nowait(payload)
            except queue.Full as err:
                sys.stderr.write(f"DiscordLogHandler queue full: {err}\n")
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        """Flushes all queued log events, waiting for pending dispatches to complete."""
        try:
            self._queue.join()
        except (RuntimeError, ValueError) as err:
            sys.stderr.write(f"DiscordLogHandler flush error: {err}\n")

    def close(self) -> None:
        """Closes the handler, stops the background worker, and cleans up resources."""
        try:
            self._queue.put_nowait(None)
            self._worker_thread.join(timeout=2.0)
        except (queue.Full, RuntimeError) as err:
            sys.stderr.write(f"DiscordLogHandler close error: {err}\n")
        super().close()


def setup_logger(
    name: str = "palworld_manager",
    log_dir: str | Path | None = None,
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    discord_webhook_url: str | None = None,
    discord_log_level: int | str = logging.ERROR,
    discord_critical_ping: str = "@thestygianarchitect",
) -> logging.Logger:
    """Configures and returns a structured logger with console, rotation, and Discord mirroring.

    Args:
        name (str): Logger name identifier.
        log_dir (str | Path | None): Directory path for persistent log files.
        log_level (int): Minimum logging severity level (default: INFO).
        max_bytes (int): Maximum size per log file before rotation (default: 10MB).
        backup_count (int): Number of rotated backup log files to retain (default: 5).
        discord_webhook_url (str | None): Optional Discord incoming webhook URL for log mirroring.
        discord_log_level (int | str): Minimum log level threshold for Discord mirroring (default: ERROR).
        discord_critical_ping (str): User/role mention string for CRITICAL alerts (default: '@thestygianarchitect').

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sensitive_filter = SensitiveDataFilter()
    logger.addFilter(sensitive_filter)

    # 1. Console Stream Handler
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(sensitive_filter)
    logger.addHandler(stream_handler)

    # 2. Rotating File Handler
    if log_dir is None:
        target_dir = Path("/var/log/palmanager") if os.name != "nt" else Path.home() / ".palmanager" / "logs"
    else:
        target_dir = Path(log_dir)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        log_file = target_dir / f"{name}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sensitive_filter)
        logger.addHandler(file_handler)
    except PermissionError as err:
        logger.warning("Permission denied initializing log directory %s: %s", target_dir, err)
    except OSError as err:
        logger.warning("OS error initializing log file handler at %s: %s", target_dir, err)

    # 3. Discord Log Mirroring Handler
    webhook = discord_webhook_url or os.getenv("PALWORLD_DISCORD_WEBHOOK_URL")
    if webhook:
        if isinstance(discord_log_level, str):
            resolved_level = getattr(logging, discord_log_level.upper(), logging.ERROR)
        else:
            resolved_level = discord_log_level

        ping = discord_critical_ping or os.getenv("PALWORLD_DISCORD_CRITICAL_PING", "@thestygianarchitect")
        discord_handler = DiscordLogHandler(webhook_url=webhook, level=resolved_level, critical_ping=ping)
        discord_handler.setFormatter(formatter)
        discord_handler.addFilter(sensitive_filter)
        logger.addHandler(discord_handler)

    return logger


# Default application-wide logger singleton
log: logging.Logger = setup_logger("palworld_web_manager")
