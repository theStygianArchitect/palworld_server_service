"""Application configuration management and dynamic INI settings source.

Provides structured environment loading, INI file value overlaying, and dynamic
hot-reloading of server configuration using Pydantic Settings and pathlib.Path.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .config_parser import parse_ini_file
from .logger import log


class PalWorldIniSettingsSource(PydanticBaseSettingsSource):
    """Custom settings source that pulls values directly from PalWorldSettings.ini.

    Enables Pydantic Settings to automatically populate configuration properties
    directly from the active PalWorldSettings.ini file on disk.

    Attributes:
        ini_path (Path): Path object pointing to the target PalWorldSettings.ini file.
    """

    def __init__(self, settings_cls: type[BaseSettings], ini_path: str | Path | None = None) -> None:
        """Initializes the INI settings source.

        Args:
            settings_cls (type[BaseSettings]): Parent Pydantic settings class.
            ini_path (str | Path | None): Optional path to PalWorldSettings.ini.
        """
        super().__init__(settings_cls)
        default_path = _resolve_default_ini_path()
        if ini_path is not None:
            self.ini_path = Path(ini_path)
        else:
            env_path = os.getenv("PALWORLD_INI_PATH")
            if env_path and Path(env_path).is_file():
                self.ini_path = Path(env_path)
            elif env_path and Path(env_path).parent.exists():
                self.ini_path = Path(env_path)
            else:
                self.ini_path = Path(default_path)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """Required abstract method implementation for Pydantic custom source.

        Args:
            field (Any): Pydantic field definition.
            field_name (str): Name of the field being resolved.

        Returns:
            tuple[Any, str, bool]: Resolved field value, name, and is_complex flag.
        """
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Reads and maps INI key-value pairs into a Pydantic settings dictionary.

        Returns:
            dict[str, Any]: Dictionary of configuration keys parsed from INI.
        """
        if not self.ini_path.exists():
            log.warning(
                "PalWorldSettings.ini not found at %s. Admin password and ports will fallback to environment/defaults.",
                self.ini_path,
            )
            return {}

        try:
            ini_data = parse_ini_file(self.ini_path)
            mapped: dict[str, Any] = {}
            for k, v in ini_data.items():
                mapped[k] = v
                mapped[k.upper()] = v
                if k == "AdminPassword":
                    mapped["ADMIN_PASSWORD"] = v
                    mapped["admin_password"] = v
                elif k == "ServerPassword":
                    mapped["SERVER_PASSWORD"] = v
                    mapped["server_password"] = v
                elif k == "ServerName":
                    mapped["SERVER_NAME"] = v
                    mapped["server_name"] = v
                elif k == "PublicPort":
                    mapped["PUBLIC_PORT"] = v
                elif k == "RCONPort":
                    mapped["RCON_PORT"] = v
                elif k == "RESTAPIPort":
                    mapped["REST_PORT"] = v
                    mapped["RESTAPIPORT"] = v
            log.info(
                "Loaded configuration from %s (AdminPassword found: %s)",
                self.ini_path,
                bool(mapped.get("AdminPassword")),
            )
            return mapped
        except FileNotFoundError as err:
            log.debug("PalWorldSettings.ini not found during settings load at %s: %s", self.ini_path, err)
            return {}
        except PermissionError as err:
            log.warning("Permission denied reading PalWorldSettings.ini at %s: %s", self.ini_path, err)
            return {}
        except OSError as err:
            log.warning("OS error reading PalWorldSettings.ini at %s: %s", self.ini_path, err)
            return {}


def _resolve_default_ini_path() -> str:
    """Finds the active PalWorldSettings.ini across standard Steam and custom directories."""
    candidate_paths = [
        Path("/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/home/steam/.steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path(
            "/home/steam/.local/share/Steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
        ),
        Path("/home/steam/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/opt/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path.home() / ".palmanager" / "PalWorldSettings.ini",
    ]
    for p in candidate_paths:
        try:
            if p.is_file():
                return str(p)
        except PermissionError as err:
            log.debug("Permission error checking candidate path %s: %s", p, err)
        except OSError as err:
            log.debug("OS error checking candidate path %s: %s", p, err)

    if os.name != "nt":
        if Path("/home/steam/.steam/steam/steamapps").exists():
            return str(candidate_paths[0])
        if Path("/home/steam/.steam/steamapps").exists():
            return str(candidate_paths[1])
        if Path("/home/steam").exists():
            return str(candidate_paths[0])
    return str(Path.home() / ".palmanager" / "PalWorldSettings.ini")


def _resolve_default_backup_dir() -> str:
    """Returns the production steam backups path if accessible, else falls back to ~/.palmanager."""
    steam_backup = Path("/home/steam/Palworld_backups")
    if os.name != "nt" and Path("/home/steam").exists():
        return str(steam_backup)
    return str(Path.home() / ".palmanager" / "Palworld_backups")


def _resolve_default_backup_repo_dir() -> str:
    """Returns a writable backup repository path, falling back to home dir if unprivileged."""
    if os.name == "nt":
        return str(Path.home() / ".palmanager" / "backups")
    var_lib = Path("/var/lib/palmanager/backups")
    try:
        var_lib.mkdir(parents=True, exist_ok=True)
        return str(var_lib)
    except PermissionError as err:
        log.debug("Permission denied creating /var/lib/palmanager/backups (%s), using home dir fallback.", err)
        return str(Path.home() / ".palmanager" / "backups")
    except OSError as err:
        log.debug("OS error creating /var/lib/palmanager/backups (%s), using home dir fallback.", err)
        return str(Path.home() / ".palmanager" / "backups")


def _resolve_default_log_dir() -> str:
    """Returns a writable log directory path, falling back to home dir if unprivileged."""
    if os.name == "nt":
        return str(Path.home() / ".palmanager" / "logs")
    var_log = Path("/var/log/palmanager")
    try:
        var_log.mkdir(parents=True, exist_ok=True)
        return str(var_log)
    except PermissionError as err:
        log.debug("Permission denied creating /var/log/palmanager (%s), using home dir fallback.", err)
        return str(Path.home() / ".palmanager" / "logs")


def _resolve_default_host_ip() -> str:
    """Discovers the active host primary LAN IP address (e.g. eth0 / 192.168.x.x)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            detected_ip = s.getsockname()[0]
            if detected_ip and not detected_ip.startswith("127."):
                return str(detected_ip)
    except OSError as err:
        log.debug("Socket probe for default LAN IP failed: %s", err)
    return "127.0.0.1"


class AppSettings(BaseSettings):
    """Primary application configuration model.

    Combines environment variables (prefixed with PALWORLD_), .env files, and
    direct PalWorldSettings.ini values with deterministic precedence.

    Attributes:
        AdminPassword (str): Server administrator password.
        ServerPassword (str): Player join password.
        ServerName (str): Dedicated server display name.
        ServerDescription (str): Extended server description.
        PublicPort (int): Game UDP port (default: 8211).
        RCONPort (int): RCON administration port (default: 25575).
        RCONEnabled (bool): Whether RCON is enabled.
        RESTAPIPort (int): Internal REST API port (default: 8212).
        RESTAPIEnabled (bool): Whether REST API is enabled.
        CrossplayPlatforms (str): Supported crossplay platforms string.
        web_port (int): Operations Suite web interface port (default: 8080).
        ini_path (str): Filepath to PalWorldSettings.ini.
        service_name (str): Target systemd service name.
        backup_repo_dir (str): Directory for isolated Git snapshots.
        backup_dir (str): Directory for server world save archives.
        log_dir (str): Directory for manager log files.
        duckdns_domain (str): Configured DuckDNS domain hostname.
        duckdns_token (str): Configured DuckDNS authentication token.
        host_ip (str): Host local/LAN IP address.
        discord_webhook_url (str | None): Discord incoming webhook URL for notifications.
        discord_log_level (str): Log level threshold for Discord mirroring (default: ERROR).
        discord_critical_ping (str): User/role mention for CRITICAL alerts (default: @thestygianarchitect).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PALWORLD_",
        extra="ignore",
        populate_by_name=True,
    )

    # Server Core & Passwords
    AdminPassword: str = Field(default="admin_password", alias="ADMIN_PASSWORD")
    ServerPassword: str = Field(default="", alias="SERVER_PASSWORD")
    ServerName: str = Field(default="Palworld Dedicated Server", alias="SERVER_NAME")
    ServerDescription: str = Field(default="", alias="SERVER_DESCRIPTION")

    # Ports & Crossplay
    PublicPort: int = Field(default=8211, alias="PUBLIC_PORT")
    RCONPort: int = Field(default=25575, alias="RCON_PORT")
    RCONEnabled: bool = Field(default=True, alias="RCON_ENABLED")
    RESTAPIPort: int = Field(default=8212, alias="REST_PORT")
    RESTAPIEnabled: bool = Field(default=True, alias="REST_ENABLED")
    CrossplayPlatforms: str = Field(default="(Steam,Xbox,PS5,Mac)", alias="CROSSPLAY_PLATFORMS")

    # Paths & Service Configurations
    web_port: int = Field(default=8080, alias="WEB_PORT")
    ini_path: str = Field(
        default_factory=_resolve_default_ini_path,
        alias="INI_PATH",
    )
    service_name: str = Field(default="palworld.service", alias="SERVICE_NAME")
    backup_repo_dir: str = Field(
        default_factory=_resolve_default_backup_repo_dir,
        alias="BACKUP_REPO_DIR",
    )
    backup_dir: str = Field(
        default_factory=_resolve_default_backup_dir,
        alias="BACKUP_DIR",
    )
    log_dir: str = Field(
        default_factory=_resolve_default_log_dir,
        alias="LOG_DIR",
    )
    duckdns_domain: str = Field(
        default="yourdomain.duckdns.org",
        validation_alias=AliasChoices(
            "PALWORLD_DOMAIN",
            "PALWORLD_SERVER_DOMAIN",
            "PALWORLD_DUCKDNS_DOMAIN",
            "DUCKDNS_DOMAIN",
            "SERVER_DOMAIN",
            "duckdns_domain",
        ),
    )
    duckdns_token: str = Field(
        default="your_duckdns_token",
        validation_alias=AliasChoices(
            "PALWORLD_DUCKDNS_TOKEN",
            "DUCKDNS_TOKEN",
            "duckdns_token",
        ),
    )
    host_ip: str = Field(
        default_factory=_resolve_default_host_ip,
        validation_alias=AliasChoices("PALWORLD_HOST_IP", "HOST_IP", "host_ip"),
    )
    discord_webhook_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PALWORLD_DISCORD_WEBHOOK_URL",
            "DISCORD_WEBHOOK_URL",
            "discord_webhook_url",
        ),
    )
    discord_log_level: str = Field(
        default="ERROR",
        validation_alias=AliasChoices(
            "PALWORLD_DISCORD_LOG_LEVEL",
            "DISCORD_LOG_LEVEL",
            "discord_log_level",
        ),
    )
    discord_critical_ping: str = Field(
        default="@thestygianarchitect",
        validation_alias=AliasChoices(
            "PALWORLD_DISCORD_CRITICAL_PING",
            "DISCORD_CRITICAL_PING",
            "discord_critical_ping",
        ),
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Configures priority order: Init args -> Env vars -> .env -> PalWorldSettings.ini.

        Args:
            settings_cls (type[BaseSettings]): Parent Pydantic settings class.
            init_settings (PydanticBaseSettingsSource): Constructor kwargs settings source.
            env_settings (PydanticBaseSettingsSource): Process environment variables source.
            dotenv_settings (PydanticBaseSettingsSource): .env file source.
            file_secret_settings (PydanticBaseSettingsSource): Secrets directory source.

        Returns:
            tuple[PydanticBaseSettingsSource, ...]: Ordered tuple of active settings sources.
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            PalWorldIniSettingsSource(settings_cls),
            file_secret_settings,
        )


_settings_instance: AppSettings | None = None


def get_settings() -> AppSettings:
    """Returns the cached singleton instance of AppSettings, instantiating if needed.

    Returns:
        AppSettings: Active application configuration settings object.
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = AppSettings()
    return _settings_instance


def reload_settings() -> AppSettings:
    """Forces re-parsing of PalWorldSettings.ini and reloads configuration singleton.

    Returns:
        AppSettings: Freshly reloaded application configuration settings object.
    """
    global _settings_instance
    _settings_instance = AppSettings()
    return _settings_instance
