import os
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .config_parser import parse_ini_file


class PalWorldIniSettingsSource(PydanticBaseSettingsSource):
    """Custom settings source that pulls values directly from PalWorldSettings.ini."""

    def __init__(self, settings_cls: type[BaseSettings], ini_path: str | None = None):
        super().__init__(settings_cls)
        self.ini_path = ini_path or os.getenv(
            "PALWORLD_INI_PATH",
            "/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
            if os.name != "nt"
            else os.path.expanduser("~/.palmanager/PalWorldSettings.ini"),
        )

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not self.ini_path or not os.path.exists(self.ini_path):
            return {}
        ini_data = parse_ini_file(str(self.ini_path))
        mapped: dict[str, Any] = {}
        for k, v in ini_data.items():
            mapped[k] = v
            mapped[k.lower()] = v
        return mapped


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="PALWORLD_",
        populate_by_name=True,
    )

    # Server Core Settings (Loaded directly from PalWorldSettings.ini or Env)
    ServerName: str = Field(default="Palworld Dedicated Server", alias="SERVER_NAME")
    ServerDescription: str = Field(default="", alias="SERVER_DESCRIPTION")
    Region: str = Field(default="", alias="REGION")
    AdminPassword: str = Field(default="", alias="ADMIN_PASSWORD")
    ServerPassword: str = Field(default="", alias="SERVER_PASSWORD")
    PublicPort: int = Field(default=8211, alias="PUBLIC_PORT")
    PublicIP: str = Field(default="", alias="PUBLIC_IP")
    RCONPort: int = Field(default=25575, alias="RCON_PORT")
    RCONEnabled: bool = Field(default=True, alias="RCON_ENABLED")
    RESTAPIPort: int = Field(default=8212, alias="REST_PORT")
    RESTAPIEnabled: bool = Field(default=True, alias="REST_ENABLED")
    CrossplayPlatforms: str = Field(default="(Steam,Xbox,PS5,Mac)", alias="CROSSPLAY_PLATFORMS")

    # Paths & Service Configurations
    web_port: int = Field(default=8080, alias="WEB_PORT")
    ini_path: str = Field(
        default="/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
        if os.name != "nt"
        else os.path.expanduser("~/.palmanager/PalWorldSettings.ini"),
        alias="INI_PATH",
    )
    service_name: str = Field(default="palworld.service", alias="SERVICE_NAME")
    backup_repo_dir: str = Field(
        default="/var/lib/palmanager/backups" if os.name != "nt" else os.path.expanduser("~/.palmanager/backups"),
        alias="BACKUP_REPO_DIR",
    )
    backup_dir: str = Field(
        default="/home/steam/Palworld_backups"
        if os.name != "nt"
        else os.path.expanduser("~/.palmanager/Palworld_backups"),
        alias="BACKUP_DIR",
    )
    log_dir: str = Field(
        default="/var/log/palmanager" if os.name != "nt" else os.path.expanduser("~/.palmanager/logs"),
        alias="LOG_DIR",
    )
    duckdns_domain: str = Field(default="yourdomain.duckdns.org", alias="SERVER_DOMAIN")
    duckdns_token: str = Field(default="your_duckdns_token", alias="DUCKDNS_TOKEN")
    host_ip: str = Field(default="127.0.0.1", alias="HOST_IP")
    discord_webhook_url: str | None = Field(
        default=None,
        validation_alias="PALWORLD_DISCORD_WEBHOOK_URL",
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
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            PalWorldIniSettingsSource(settings_cls),
            file_secret_settings,
        )


def get_settings() -> AppSettings:
    return AppSettings()


settings = get_settings()


def reload_settings() -> AppSettings:
    global settings
    settings = AppSettings()
    return settings
