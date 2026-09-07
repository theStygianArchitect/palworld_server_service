"""Palworld Dedicated Server Operations Suite & Web Management Plane.

Top-level namespace providing unified access and backward-compatible re-exports
for core infrastructure, config pipelines, engine operations, monitoring, and API schemas.
"""

from __future__ import annotations

from app.api.schemas import GameplaySettingsSchema
from app.config_manager.parser import parse_ini_file, serialize_ini_settings
from app.config_manager.pipeline import ConfigPipeline
from app.core.config import AppSettings, get_settings, reload_settings
from app.core.logger import log, setup_logger
from app.engine.notifications import DiscordNotifier
from app.engine.service import PalEngine
from app.monitoring.log_scraper import PalLogScraper
from app.monitoring.net_diagnostics import NetworkThroughputTracker
from app.monitoring.tracker import CommunityTracker

__version__ = "1.0.0"

__all__ = [
    "AppSettings",
    "CommunityTracker",
    "ConfigPipeline",
    "DiscordNotifier",
    "GameplaySettingsSchema",
    "NetworkThroughputTracker",
    "PalEngine",
    "PalLogScraper",
    "__version__",
    "get_settings",
    "log",
    "parse_ini_file",
    "reload_settings",
    "serialize_ini_settings",
    "setup_logger",
]
