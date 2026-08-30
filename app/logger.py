import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


class SensitiveDataFilter(logging.Filter):
    """Redacts sensitive credentials, passwords, and tokens from log messages."""

    PATTERNS = [
        (re.compile(r'(?i)(admin_password|password|token|secret)\s*[:=]\s*["\']?([^"\'\s,]+)'), r"\1=[REDACTED]"),
        (re.compile(r"(?i)(Basic|Bearer)\s+[A-Za-z0-9+/=._-]+"), r"\1 [REDACTED]"),
        (re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"), r"[REDACTED_UUID]"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            msg = record.msg
            for pattern, replacement in self.PATTERNS:
                msg = pattern.sub(replacement, msg)
            record.msg = msg
        return True


def setup_logger(
    name: str = "palworld_manager",
    log_dir: Optional[str] = None,
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> logging.Logger:
    """Configures and returns a structured logger with console output and log rotation."""
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Avoid duplicate handlers if already configured
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
        log_dir = (
            "/var/log/palmanager"
            if os.name != "nt"
            else os.path.expanduser("~/.palmanager/logs")
        )

    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = Path(log_dir) / f"{name}.log"
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
    except Exception as e:
        logger.warning(f"Could not initialize RotatingFileHandler at {log_dir}: {e}")

    return logger


# Default application-wide logger
log = setup_logger("palworld_web_manager")
