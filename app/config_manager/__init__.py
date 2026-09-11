"""Configuration Manager domain: PalWorldSettings.ini grammar parsing, serialization, and security pipeline."""

from __future__ import annotations

from app.core.atomic_io import atomic_write_ini

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
    "atomic_write_ini",
    "parse_ini_file",
    "serialize_ini_settings",
]
