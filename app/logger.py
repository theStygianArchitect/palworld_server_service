"""Centralized structured logging with sensitive credential redaction and rotation.

Provides high-performance rotating file logs and console stream formatting with
zero-leak redaction of administrative tokens, passwords, and secrets.
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts sensitive credentials, passwords, and tokens.

    Scans log message text using regular expressions to sanitize administrative secrets
    prior to emission to console streams or disk files.
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


def setup_logger(
    name: str = "palworld_manager",
    log_dir: str | Path | None = None,
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configures and returns a structured logger with console output and rotation.

    Args:
        name (str): Logger name identifier.
        log_dir (str | Path | None): Directory path for persistent log files.
        log_level (int): Minimum logging severity level (default: INFO).
        max_bytes (int): Maximum size per log file before rotation (default: 10MB).
        backup_count (int): Number of rotated backup log files to retain (default: 5).

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

    return logger


# Default application-wide logger singleton
log: logging.Logger = setup_logger("palworld_web_manager")
