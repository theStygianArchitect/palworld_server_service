"""Application configuration management and dynamic INI settings source.

Provides structured environment loading, INI file value overlaying, and dynamic
hot-reloading of server configuration using Pydantic Settings and pathlib.Path.
"""

from __future__ import annotations

import datetime
import os
import socket
import ssl
from pathlib import Path
from typing import Any

import psutil
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.api.schemas import TLSCertificateInfo

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
            if env_path and (Path(env_path).is_file() or Path(env_path).parent.exists()):
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
            # pylint: disable=import-outside-toplevel
            # Rationale: Defer config_manager parser import to decouple core domain from grammar parsing.
            from app.config_manager.parser import parse_ini_file

            ini_data = parse_ini_file(self.ini_path)
            alias_map: dict[str, tuple[str, ...]] = {
                "AdminPassword": ("ADMIN_PASSWORD", "admin_password"),
                "ServerPassword": ("SERVER_PASSWORD", "server_password"),
                "ServerName": ("SERVER_NAME", "server_name"),
                "PublicPort": ("PUBLIC_PORT",),
                "QueryPort": ("QUERY_PORT", "query_port"),
                "RCONPort": ("RCON_PORT",),
                "RCONEnabled": ("RCON_ENABLED", "RCONEnabled", "rcon_enabled"),
                "bRCONEnabled": ("RCON_ENABLED", "RCONEnabled", "rcon_enabled"),
                "RESTAPIPort": ("REST_PORT", "RESTAPIPORT"),
                "RESTAPIEnabled": ("REST_ENABLED", "RESTAPIEnabled", "rest_enabled"),
                "bRESTAPIEnabled": ("REST_ENABLED", "RESTAPIEnabled", "rest_enabled"),
            }
            mapped: dict[str, Any] = {}
            for k, v in ini_data.items():
                mapped[k] = v
                mapped[k.upper()] = v
                for alias in alias_map.get(k, ()):
                    mapped[alias] = v
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


def is_posix() -> bool:
    """Returns True if running on POSIX (Linux/macOS) and False on Windows/NT."""
    return os.name == "posix"


def resolve_palworld_ini_path() -> Path:
    """Finds the active PalWorldSettings.ini across standard Steam and custom directories."""
    candidate_paths = [
        Path("/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path(
            "/home/steam/.local/share/Steam/steamapps/common/PalServer"
            "/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
        ),
        Path("/home/steam/PalServer/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path("/opt/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"),
        Path.home() / ".palmanager" / "PalWorldSettings.ini",
    ]
    for p in candidate_paths:
        try:
            if p.is_file():
                return p
        except PermissionError as err:
            log.debug("Permission error checking candidate path %s: %s", p, err)
        except OSError as err:
            log.debug("OS error checking candidate path %s: %s", p, err)

    if os.name != "nt":
        if Path("/home/steam/.steam/steam/steamapps").exists():
            return candidate_paths[0]
        if Path("/home/steam/Steam/steamapps").exists():
            return candidate_paths[1]
        return candidate_paths[3]
    return Path.home() / ".palmanager" / "PalWorldSettings.ini"


def _probe_socket_lan_ip() -> str | None:
    """Probes default routing socket for outbound LAN interface IP."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            detected_ip = s.getsockname()[0]
            if detected_ip and not detected_ip.startswith("127."):
                return str(detected_ip)
    except OSError as err:
        log.debug("Socket routing LAN IP probe failed: %s", err)
    return None


def _probe_iface_lan_ip() -> str | None:
    """Scans physical and virtual network interfaces for non-loopback IPv4 addresses."""
    try:
        for iface_name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if (
                    addr.family == socket.AF_INET
                    and not addr.address.startswith("127.")
                    and iface_name.startswith(("eth", "en", "wl", "bond", "Ethernet", "Wi-Fi"))
                ):
                    return str(addr.address)
    except psutil.Error as err:
        log.debug("Psutil network interface probe failed: %s", err)
    except OSError as err:
        log.debug("OS error probing network interface: %s", err)
    except KeyError as err:
        log.debug("Key error probing network interface: %s", err)
    except AttributeError as err:
        log.debug("Attribute error probing network interface: %s", err)
    return None


def _probe_hostname_lan_ip() -> str | None:
    """Resolves local hostname against DNS/hosts database."""
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        if host_ip and not host_ip.startswith("127."):
            return host_ip
    except socket.gaierror as err:
        log.debug("Hostname LAN IP resolution gaierror: %s", err)
    except socket.herror as err:
        log.debug("Hostname LAN IP resolution herror: %s", err)
    except OSError as err:
        log.debug("Hostname LAN IP resolution OS error: %s", err)
    return None


def resolve_host_lan_ip() -> str:
    """Discovers the active host primary LAN IP address (e.g. eth0 / 192.168.x.x)."""
    env_ip = os.getenv("PALWORLD_HOST_IP") or os.getenv("HOST_IP")
    if env_ip and env_ip != "127.0.0.1":
        return env_ip

    return _probe_socket_lan_ip() or _probe_iface_lan_ip() or _probe_hostname_lan_ip() or "127.0.0.1"


def _resolve_default_ini_path() -> str:
    """Finds the active PalWorldSettings.ini across standard Steam and custom directories."""
    return str(resolve_palworld_ini_path())


def _resolve_default_backup_dir() -> str:
    """Returns the production steam backups path if accessible, else falls back to ~/.palmanager."""
    steam_backup = Path("/home/steam/Palworld_backups")
    if os.name != "nt" and Path("/home/steam").exists():
        return str(steam_backup)
    return str(Path.home() / ".palmanager" / "Palworld_backups")


def _is_directory_writable(target_dir: Path) -> bool:
    """Verifies whether the target directory can be written to by the current process.

    Args:
        target_dir: Filesystem directory path to evaluate.

    Returns:
        bool: True if process has write access, False otherwise.
    """
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        probe = target_dir / ".write_probe"
        probe.touch()
        probe.unlink()
        return True
    except PermissionError as err:
        log.debug("Permission denied accessing directory %s: %s", target_dir, err)
        return False
    except OSError as err:
        log.debug("OS error accessing directory %s: %s", target_dir, err)
        return False


def _resolve_default_log_dir() -> str:
    """Returns a writable log directory path, falling back to home dir if unprivileged."""
    if os.name == "nt":
        return str(Path.home() / ".palmanager" / "logs")
    var_log = Path("/var/log/palmanager")
    if _is_directory_writable(var_log):
        return str(var_log)
    return str(Path.home() / ".palmanager" / "logs")


def _resolve_default_db_path() -> str:
    """Returns a writable SQLite database path, falling back to home dir if unprivileged."""
    if os.name == "nt":
        return str(Path.home() / ".palmanager" / "palmanager.db")
    var_lib = Path("/var/lib/palmanager")
    if _is_directory_writable(var_lib):
        return str(var_lib / "palmanager.db")
    return str(Path.home() / ".palmanager" / "palmanager.db")


def _resolve_default_metrics_db_path() -> str:
    """Returns a writable SQLite metrics database path, falling back to home dir if unprivileged."""
    if os.name == "nt":
        return str(Path.home() / ".palmanager" / "metrics.db")
    var_lib = Path("/var/lib/palmanager")
    if _is_directory_writable(var_lib):
        return str(var_lib / "metrics.db")
    return str(Path.home() / ".palmanager" / "metrics.db")


def _resolve_default_host_ip() -> str:
    """Discovers the active host primary LAN IP address (e.g. eth0 / 192.168.x.x)."""
    return resolve_host_lan_ip()


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
        QueryPort (int): Steam query UDP port (default: 27015).
        RCONPort (int): RCON administration port (default: 25575).
        RCONEnabled (bool): Whether RCON is enabled.
        RESTAPIPort (int): Internal REST API port (default: 8212).
        RESTAPIEnabled (bool): Whether REST API is enabled.
        CrossplayPlatforms (str): Supported crossplay platforms string.
        web_port (int): Operations Suite web interface port (default: 8080).
        ini_path (str): Filepath to PalWorldSettings.ini.
        service_name (str): Target systemd service name.
        backup_dir (str): Directory for server world save archives.
        log_dir (str): Directory for manager log files.
        database_path (str): Filepath to SQLite database.
        metrics_db_path (str): Filepath to SQLite time-series metrics database.
        metrics_retention_days (int): Rolling retention window in days for telemetry metrics (default: 30).
        metrics_sample_interval_seconds (int): In-memory telemetry sampling interval in seconds (default: 10).
        metrics_flush_interval_seconds (int): Periodic disk flush interval in seconds for batched telemetry
            snapshots (default: 300 / 5 minutes).
        duckdns_domain (str): Configured DuckDNS domain hostname.
        duckdns_token (str): Configured DuckDNS authentication token.
        host_ip (str): Host local/LAN IP address.
        discord_webhook_url (str | None): Discord incoming webhook URL for notifications.
        discord_log_level (str): Log level threshold for Discord mirroring (default: ERROR).
        discord_critical_ping (str): User/role mention for CRITICAL alerts (default: @thestygianarchitect).
        github_repo_url (str): Upstream GitHub repository URL for template issues and feedback redirects.
        admin_credential_export_path (str | None): Optional filesystem path for initial administrator credential export.
        updater_enabled (bool): Whether background upstream commit and update watcher is active (default: True).
        update_check_interval_seconds (int): Periodic interval in seconds to poll upstream GitHub (default: 600 / 10m).
        update_branch (str): Target git branch to poll and deploy (default: 'main').
        deploy_script_path (str): Filepath to host zero-drift deployer script
            (default: '/opt/palworld-web-manager/scripts/deploy.sh').
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
    QueryPort: int = Field(
        default=27015,
        alias="QUERY_PORT",
        validation_alias=AliasChoices("PALWORLD_QUERY_PORT", "QUERY_PORT", "query_port"),
    )
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
    backup_dir: str = Field(
        default_factory=_resolve_default_backup_dir,
        alias="BACKUP_DIR",
    )
    log_dir: str = Field(
        default_factory=_resolve_default_log_dir,
        alias="LOG_DIR",
    )
    database_path: str = Field(
        default_factory=_resolve_default_db_path,
        validation_alias=AliasChoices("PALWORLD_DATABASE_PATH", "DATABASE_PATH", "database_path"),
    )
    metrics_db_path: str = Field(
        default_factory=_resolve_default_metrics_db_path,
        validation_alias=AliasChoices("PALWORLD_METRICS_DB_PATH", "METRICS_DB_PATH", "metrics_db_path"),
    )
    metrics_retention_days: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "PALWORLD_METRICS_RETENTION_DAYS", "METRICS_RETENTION_DAYS", "metrics_retention_days"
        ),
    )
    metrics_sample_interval_seconds: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "PALWORLD_METRICS_SAMPLE_INTERVAL_SECONDS",
            "METRICS_SAMPLE_INTERVAL_SECONDS",
            "metrics_sample_interval_seconds",
        ),
    )
    metrics_flush_interval_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "PALWORLD_METRICS_FLUSH_INTERVAL_SECONDS",
            "METRICS_FLUSH_INTERVAL_SECONDS",
            "metrics_flush_interval_seconds",
        ),
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
    github_repo_url: str = Field(
        default="https://github.com/theStygianArchitect/palworld_server_service",
        validation_alias=AliasChoices(
            "PALWORLD_GITHUB_REPO_URL",
            "GITHUB_REPO_URL",
            "github_repo_url",
        ),
    )
    admin_credential_export_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PALWORLD_ADMIN_CREDENTIAL_EXPORT_PATH",
            "ADMIN_CREDENTIAL_EXPORT_PATH",
            "admin_credential_export_path",
        ),
    )
    updater_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PALWORLD_UPDATER_ENABLED",
            "UPDATER_ENABLED",
            "updater_enabled",
        ),
    )
    update_check_interval_seconds: int = Field(
        default=600,
        ge=10,
        validation_alias=AliasChoices(
            "PALWORLD_UPDATE_CHECK_INTERVAL_SECONDS",
            "UPDATE_CHECK_INTERVAL_SECONDS",
            "update_check_interval_seconds",
        ),
    )
    update_branch: str = Field(
        default="main",
        validation_alias=AliasChoices(
            "PALWORLD_UPDATE_BRANCH",
            "UPDATE_BRANCH",
            "update_branch",
        ),
    )
    deploy_script_path: str = Field(
        default="/opt/palworld-web-manager/scripts/deploy.sh",
        validation_alias=AliasChoices(
            "PALWORLD_DEPLOY_SCRIPT_PATH",
            "DEPLOY_SCRIPT_PATH",
            "deploy_script_path",
        ),
    )

    # HTTPS / TLS Configuration
    ssl_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("PALWORLD_SSL_ENABLED", "SSL_ENABLED", "ssl_enabled"),
    )
    ssl_cert_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PALWORLD_SSL_CERT_PATH", "SSL_CERT_PATH", "ssl_cert_path"),
    )
    ssl_key_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PALWORLD_SSL_KEY_PATH", "SSL_KEY_PATH", "ssl_key_path"),
    )
    ssl_port: int = Field(
        default=8443,
        validation_alias=AliasChoices("PALWORLD_SSL_PORT", "SSL_PORT", "ssl_port"),
    )
    ssl_auto_detect: bool = Field(
        default=True,
        validation_alias=AliasChoices("PALWORLD_SSL_AUTO_DETECT", "SSL_AUTO_DETECT", "ssl_auto_detect"),
    )
    letsencrypt_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PALWORLD_LETSENCRYPT_EMAIL", "LETSENCRYPT_EMAIL", "letsencrypt_email"),
    )

    @field_validator("github_repo_url")
    @classmethod
    def validate_github_repo_url(cls, v: str) -> str:
        """Validates that github_repo_url starts with http:// or https:// and strips trailing slashes."""
        cleaned = v.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("github_repo_url must start with http:// or https://")
        return cleaned.rstrip("/")

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


# pylint: disable=invalid-name
# Rationale: Standard lowercase leading-underscore naming for module-level singleton instance.
_settings_instance: AppSettings | None = None


def get_settings() -> AppSettings:
    """Returns the cached singleton instance of AppSettings, instantiating if needed.

    Returns:
        AppSettings: Active application configuration settings object.
    """
    # pylint: disable=global-statement
    # Rationale: Module singleton pattern requires updating module-level reference.
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = AppSettings()
    return _settings_instance


def reload_settings() -> AppSettings:
    """Forces re-parsing of PalWorldSettings.ini and reloads configuration singleton.

    Returns:
        AppSettings: Freshly reloaded application configuration settings object.
    """
    # pylint: disable=global-statement
    # Rationale: Module singleton pattern requires updating module-level reference.
    global _settings_instance
    _settings_instance = AppSettings()
    return _settings_instance


def resolve_admin_credential_export_path(custom_path: str | Path | None = None) -> Path:
    """Resolves the destination filepath for writing initial administrator credentials out-of-band.

    Args:
        custom_path: Optional override path from configuration or arguments.

    Returns:
        Path: Resolved absolute Path object with ensured parent directory existence.
    """
    if custom_path:
        target = Path(custom_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    # Default resolution: On POSIX when /etc is writable (root), use /etc/palmanager;
    # otherwise fallback to user home directory ~/.palmanager
    if os.name != "nt" and Path("/etc").is_dir() and os.access("/etc", os.W_OK):
        etc_dir = Path("/etc/palmanager")
        etc_dir.mkdir(parents=True, exist_ok=True)
        return etc_dir / "initial_admin_credential.txt"

    home_dir = Path.home() / ".palmanager"
    home_dir.mkdir(parents=True, exist_ok=True)
    return home_dir / "initial_admin_credential.txt"


def _extract_dn_field(dn_tuples: tuple[Any, ...], field_name: str) -> str | None:
    """Extracts a specific attribute (e.g. commonName) from decoded X.509 RDN tuples.

    Args:
        dn_tuples: Decoded Relative Distinguished Name structure from ssl._ssl._test_decode_cert.
        field_name: Target attribute key name.

    Returns:
        str | None: String value if present, else None.
    """
    for rdn in dn_tuples:
        for key, val in rdn:
            if key == field_name:
                return str(val)
    return None


def _decode_x509_file(path_str: str) -> dict[str, Any] | None:
    """Invokes CPython's internal _ssl._test_decode_cert with safe fallback."""
    # pylint: disable=protected-access,assignment-from-no-return
    # Rationale: CPython standard library _ssl._test_decode_cert returns dict of parsed ASN.1 fields.
    c_ssl: Any = getattr(ssl, "_ssl", None)
    if c_ssl is None or not hasattr(c_ssl, "_test_decode_cert"):
        log.warning("CPython internal _ssl._test_decode_cert is unavailable on this Python runtime")
        return None
    raw_dict: dict[str, Any] = c_ssl._test_decode_cert(path_str)
    return raw_dict


def _build_tls_cert_info(decoded: dict[str, Any]) -> TLSCertificateInfo:
    """Builds a TLSCertificateInfo model from a decoded X.509 cert dictionary."""
    subject_cn = _extract_dn_field(decoded.get("subject", ()), "commonName") or "Unknown"
    issuer_cn = (
        _extract_dn_field(decoded.get("issuer", ()), "commonName")
        or _extract_dn_field(decoded.get("issuer", ()), "organizationName")
        or "Unknown"
    )

    not_after_str = decoded.get("notAfter", "")
    not_before_str = decoded.get("notBefore", "")

    dt_expires = (
        datetime.datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
        if not_after_str
        else datetime.datetime.now(datetime.timezone.utc)
    )
    dt_valid_from = (
        datetime.datetime.strptime(not_before_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
        if not_before_str
        else datetime.datetime.now(datetime.timezone.utc)
    )

    dt_now = datetime.datetime.now(datetime.timezone.utc)
    days_remaining = (dt_expires - dt_now).days

    san_tuples = decoded.get("subjectAltName", ())
    san_list = [str(val) for kind, val in san_tuples if kind in ("DNS", "IP Address")]

    return TLSCertificateInfo(
        subject=subject_cn,
        issuer=issuer_cn,
        valid_from=dt_valid_from.isoformat(),
        expires_at=dt_expires.isoformat(),
        days_remaining=days_remaining,
        is_expired=dt_now > dt_expires,
        san_list=san_list,
    )


def inspect_certificate(cert_path: Path | str) -> TLSCertificateInfo | None:
    """Inspects and parses an X.509 PEM certificate file using pure standard library.

    Args:
        cert_path: Filesystem path to the certificate PEM file.

    Returns:
        TLSCertificateInfo | None: Populated metadata model if valid, or None if missing/invalid.
    """
    path_obj = Path(cert_path).expanduser().resolve()
    if not path_obj.is_file():
        log.debug("Certificate file does not exist at %s", path_obj)
        return None

    res: TLSCertificateInfo | None = None
    try:
        decoded = _decode_x509_file(str(path_obj))
        if decoded:
            res = _build_tls_cert_info(decoded)
        else:
            log.warning("Certificate at %s decoded to empty payload", path_obj)
    except FileNotFoundError as err:
        log.warning("Certificate file not found at %s: %s", path_obj, err)
    except PermissionError as err:
        log.warning("Permission denied reading certificate at %s: %s", path_obj, err)
    except OSError as err:
        log.warning("OS error decoding certificate at %s: %s", path_obj, err)
    except ValueError as err:
        log.warning("Value error decoding certificate at %s: %s", path_obj, err)
    except KeyError as err:
        log.warning("Key error decoding certificate fields at %s: %s", path_obj, err)

    return res


def resolve_ssl_paths(settings_obj: AppSettings | None = None) -> tuple[Path, Path] | None:
    """Discovers and validates active SSL/TLS certificate and private key filepaths.

    Validates that candidate certificate and private key files exist, are readable,
    and form a cryptographically matching key pair via ssl.SSLContext.load_cert_chain.

    Args:
        settings_obj: Optional AppSettings instance (defaults to get_settings()).

    Returns:
        tuple[Path, Path] | None: Valid (cert_path, key_path) pair, or None if unavailable.
    """
    cfg = settings_obj or get_settings()

    candidates: list[tuple[Path, Path]] = []

    # 1. Explicitly configured paths
    if cfg.ssl_cert_path and cfg.ssl_key_path:
        candidates.append((Path(cfg.ssl_cert_path), Path(cfg.ssl_key_path)))

    # 2. Standard auto-detection paths
    if cfg.ssl_auto_detect or cfg.ssl_enabled:
        candidates.append(
            (
                Path("/var/lib/palmanager/certs/fullchain.pem"),
                Path("/var/lib/palmanager/certs/privkey.pem"),
            )
        )
        if cfg.duckdns_domain:
            clean_domain = cfg.duckdns_domain.strip().lower()
            candidates.append(
                (
                    Path(f"/etc/letsencrypt/live/{clean_domain}/fullchain.pem"),
                    Path(f"/etc/letsencrypt/live/{clean_domain}/privkey.pem"),
                )
            )
        candidates.append(
            (
                Path.home() / ".palmanager" / "certs" / "fullchain.pem",
                Path.home() / ".palmanager" / "certs" / "privkey.pem",
            )
        )

    for cert_candidate, key_candidate in candidates:
        try:
            resolved_cert = cert_candidate.expanduser().resolve()
            resolved_key = key_candidate.expanduser().resolve()
            if not resolved_cert.is_file() or not resolved_key.is_file():
                continue

            # Cryptographic validation of certificate and private key match
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=str(resolved_cert), keyfile=str(resolved_key))
            log.info("Validated active TLS certificate pair at %s and %s", resolved_cert, resolved_key)
            return resolved_cert, resolved_key
        except FileNotFoundError as err:
            log.debug("TLS path candidate %s / %s not found: %s", cert_candidate, key_candidate, err)
        except PermissionError as err:
            log.debug("TLS path candidate %s / %s permission denied: %s", cert_candidate, key_candidate, err)
        except ssl.SSLError as err:
            log.debug("TLS path candidate %s / %s SSL mismatch: %s", cert_candidate, key_candidate, err)
        except OSError as err:
            log.debug("TLS path candidate %s / %s OS error: %s", cert_candidate, key_candidate, err)

    return None
