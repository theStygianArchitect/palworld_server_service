"""Configuration Security Pipeline and Protected Key Isolation.

Enforces zero-trust isolation of administrative keys (passwords, ports, webhooks),
preventing leakage into public web UI views and merging public edits safely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config_parser import parse_ini_file, serialize_ini_settings

PROTECTED_ADMIN_KEYS: set[str] = {
    "AdminPassword",
    "ServerPassword",
    "BanListURL",
    "PublicPort",
    "PublicIP",
    "RCONEnabled",
    "RCONPort",
    "RESTAPIEnabled",
    "RESTAPIPort",
    "bUseAuth",
}


class ConfigPipeline:
    """Isolates sensitive administrative credentials from public-facing web interfaces.

    Attributes:
        ini_path (Path): Path object pointing to the target PalWorldSettings.ini file.
    """

    def __init__(self, ini_path: str | Path) -> None:
        """Initializes the security pipeline with a target INI file path.

        Args:
            ini_path (str | Path): Filepath to the PalWorldSettings.ini configuration file.
        """
        self.ini_path = Path(ini_path)

    def read_to_json(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Reads configuration and separates into public and protected dictionaries.

        Returns:
            tuple[dict[str, Any], dict[str, Any]]: A tuple containing (public_view, protected_view).

        Raises:
            FileNotFoundError: If the INI configuration file does not exist.
        """
        if not self.ini_path.exists():
            raise FileNotFoundError(f"Configuration not found at: {self.ini_path}")

        full_state = parse_ini_file(self.ini_path)
        public_view = {k: v for k, v in full_state.items() if k not in PROTECTED_ADMIN_KEYS}
        protected_view = {k: v for k, v in full_state.items() if k in PROTECTED_ADMIN_KEYS}
        return public_view, protected_view

    def get_public_view(self) -> dict[str, Any]:
        """Returns only public gameplay settings, hiding protected keys.

        Returns:
            dict[str, Any]: Dictionary of public gameplay settings safe for web UI display.
        """
        if not self.ini_path.exists():
            return {}
        public_view, _ = self.read_to_json()
        return public_view

    def merge_and_serialize(self, incoming_sanitized_json: dict[str, Any]) -> str:
        """Merges public gameplay changes with protected administrative keys.

        Prevents web users from overwriting or viewing administrative secrets.

        Args:
            incoming_sanitized_json (dict[str, Any]): Validated public gameplay settings.

        Returns:
            str: Complete serialized INI string ready for disk persistence.
        """
        if self.ini_path.exists():
            _, live_protected = self.read_to_json()
            live_full = parse_ini_file(self.ini_path)
        else:
            live_protected = {}
            live_full = {}

        for key, val in incoming_sanitized_json.items():
            if key not in PROTECTED_ADMIN_KEYS:
                live_full[key] = val

        for key, val in live_protected.items():
            live_full[key] = val

        return serialize_ini_settings(live_full)
