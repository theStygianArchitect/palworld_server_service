"""Configuration Manager domain: PalWorldSettings.ini grammar parsing, serialization, and security pipeline."""

from __future__ import annotations

from .parser import (
    SETTING_METADATA,
    parse_ini_file,
    serialize_ini_settings,
)
from .pipeline import (
    PROTECTED_ADMIN_KEYS,
    ConfigPipeline,
)

__all__ = [
    "PROTECTED_ADMIN_KEYS",
    "SETTING_METADATA",
    "ConfigPipeline",
    "parse_ini_file",
    "serialize_ini_settings",
]
