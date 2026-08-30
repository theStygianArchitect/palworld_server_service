from pathlib import Path
from typing import Any, Dict, Tuple
from .config_parser import parse_ini_file, serialize_ini_settings

PROTECTED_ADMIN_KEYS = {
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
    def __init__(self, ini_path: str):
        self.ini_path = Path(ini_path)

    def read_to_json(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not self.ini_path.exists():
            raise FileNotFoundError(f"Configuration not found at: {self.ini_path}")

        full_state = parse_ini_file(str(self.ini_path))
        public_view = {k: v for k, v in full_state.items() if k not in PROTECTED_ADMIN_KEYS}
        protected_view = {k: v for k, v in full_state.items() if k in PROTECTED_ADMIN_KEYS}
        return public_view, protected_view

    def merge_and_serialize(self, incoming_sanitized_json: Dict[str, Any]) -> str:
        if self.ini_path.exists():
            _, live_protected = self.read_to_json()
            live_full = parse_ini_file(str(self.ini_path))
        else:
            live_protected = {}
            live_full = {}

        for key, val in incoming_sanitized_json.items():
            if key not in PROTECTED_ADMIN_KEYS:
                live_full[key] = val

        for key, val in live_protected.items():
            live_full[key] = val

        return serialize_ini_settings(live_full)
