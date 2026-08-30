"""Physical schema definitions and domain models for Palworld Operations Suite.

All request payloads, gameplay settings constraints, and structured telemetry models
are strictly isolated here in compliance with 3 AM Architecture & Type Isolation standards.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from pydantic import BaseModel, Field, field_validator

# =========================================================================
# 1. Gameplay Settings Schema
# =========================================================================


class GameplaySettingsSchema(BaseModel):
    """Pydantic validation schema for Palworld INI gameplay configuration values."""

    ServerName: str | None = Field(default="The Cool Kids Palworld Server", max_length=128)
    ServerDescription: str | None = Field(default="", max_length=256)
    Region: str | None = Field(default="", max_length=32)

    DayTimeSpeedRate: float | None = Field(default=1.0, ge=0.1, le=10.0)
    NightTimeSpeedRate: float | None = Field(default=1.0, ge=0.1, le=10.0)
    ExpRate: float | None = Field(default=1.0, ge=0.1, le=20.0)
    PalCaptureRate: float | None = Field(default=1.0, ge=0.5, le=5.0)
    PalSpawnNumRate: float | None = Field(default=1.0, ge=0.5, le=3.0)
    PalDamageRateAttack: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PalDamageRateDefense: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PlayerDamageRateAttack: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PlayerDamageRateDefense: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PlayerStomachDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)
    PlayerStaminaDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)
    PalStomachDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)
    PalStaminaDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)

    CollectionDropRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    CollectionObjectHpRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    CollectionObjectRespawnSpeedRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    EnemyDropItemRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    DeathPenalty: str | None = Field(default="None")
    bEnablePlayerToPlayerDamage: bool | None = False
    bEnableFriendlyFire: bool | None = False
    bEnableInvaderEnemy: bool | None = True
    bActiveUNKO: bool | None = False

    BaseCampMaxNum: int | None = Field(default=128, ge=1, le=256)
    BaseCampWorkerMaxNum: int | None = Field(default=20, ge=1, le=50)
    GuildPlayerMaxNum: int | None = Field(default=20, ge=1, le=100)
    PalEggDefaultHatchingTime: float | None = Field(default=0.0, ge=0.0, le=240.0)
    WorkSpeedRate: float | None = Field(default=1.0, ge=0.1, le=10.0)

    CrossplayPlatforms: str | None = Field(default="(Steam,Xbox,PS5,Mac)")
    bIsMultiplay: bool | None = False
    bShowPlayerList: bool | None = False
    bIsShowJoinLeftMessage: bool | None = True
    SupplyDropSpan: int | None = Field(default=180, ge=0, le=1000)

    @field_validator("ServerName", "ServerDescription", "Region")
    @classmethod
    def sanitize_strings(cls, v: str | None) -> str:
        if v is None:
            return ""
        return re.sub(r'[\r\n\t"]', "", v).strip()

    @field_validator("DeathPenalty")
    @classmethod
    def validate_death_penalty(cls, v: str | None) -> str:
        allowed = {"None", "Item", "ItemAndEquipment", "All"}
        if v in allowed:
            return str(v)
        return "None"


# =========================================================================
# 2. Ingress Request Payloads (Physically isolated from Route Handlers)
# =========================================================================


class SettingsUpdateRequest(BaseModel):
    """Payload for updating INI configuration with gameplay settings dictionary."""

    settings: dict[str, Any] = Field(..., description="Key-value mapping of updated gameplay settings")


class RebootRequest(BaseModel):
    """Payload for scheduling a graceful or immediate server restart."""

    countdown_seconds: int = Field(default=60, ge=0, le=3600, description="Countdown duration in seconds")
    trigger_steam_update: bool = Field(default=False, description="Whether to trigger a SteamCMD update flag")
    update_version_tag: str = Field(default="", max_length=64, description="Target update release version tag")
    custom_message: str = Field(default="", max_length=256, description="Optional custom broadcast notice")
    settings: dict[str, Any] | None = Field(
        default=None, description="Optional updated settings to persist prior to reboot"
    )


class PlayerKickRequest(BaseModel):
    """Payload for administrative player kick action."""

    player_id: str = Field(..., min_length=1, max_length=64, description="Unique target player ID")
    message: str = Field(default="Kicked by administrator", max_length=128, description="Notice sent to player")


class PlayerBanRequest(BaseModel):
    """Payload for administrative player ban action."""

    player_id: str = Field(..., min_length=1, max_length=64, description="Unique target player ID")
    message: str = Field(default="Banned by administrator", max_length=128, description="Reason for ban")


class PlayerWarnRequest(BaseModel):
    """Payload for administrative in-game HUD broadcast notice."""

    message: str = Field(..., min_length=1, max_length=256, description="Announcement text shown to all players")


class SettingsRestoreRequest(BaseModel):
    """Payload for restoring configuration from a Git snapshot commit hash."""

    commit_hash: str = Field(..., min_length=4, max_length=64, description="Target Git commit hash to roll back to")


# =========================================================================
# 3. Structured Telemetry & Domain TypedDicts
# =========================================================================


class TopBadgeInfo(TypedDict):
    label: str
    style: str
    dot: str


class LogScraperInfo(TypedDict):
    registered: bool
    session_id: str | None
    first_seen: str | None
    last_line: str
    status_label: str
    status_color: str
    crossplay_platforms: str


class PocketpairMasterInfo(TypedDict):
    listed: bool
    server_id: str
    name: str
    version: str
    status_label: str
    status_color: str


class NetworkMatrixInfo(TypedDict):
    public_ip: str
    dns_ip: str
    domain: str
    is_aligned: bool
    lan_ip: str
    public_port: int
    direct_connect_addr: str
    lan_connect_addr: str


class DiscoveryHubPayload(TypedDict):
    log_scraper: LogScraperInfo
    pocketpair_master: PocketpairMasterInfo
    network_matrix: NetworkMatrixInfo


class SteamA2SInfo(TypedDict):
    responsive: bool
    ping_ms: float | None
    server_name: str
    map_name: str
    folder: str
    game: str


class SecurityMatrixInfo(TypedDict):
    is_password_protected: bool
    password_status_label: str
    server_password: str
    rcon_port: int
    rest_port: int
    max_players: int
    current_players: int
    slot_capacity_label: str


class PlayerRecord(TypedDict):
    playerId: str
    userId: str
    name: str
    level: int
    ping: float
    location: dict[str, float]
    status: str
    last_seen: str


class PlayerLedgerMatrix(TypedDict):
    active_count: int
    total_registered: int
    active_players: list[PlayerRecord]
    offline_players: list[PlayerRecord]


class HardwareTelemetryInfo(TypedDict):
    host_ram_used_gb: float
    host_ram_total_gb: float
    host_ram_pct: float
    cgroup_ram_used_gb: float
    cgroup_limit_gb: float
    cgroup_ram_pct: float
    swap_used_gb: float
    swap_total_gb: float
    swap_pct: float
    cpu_cores: list[float]
    cpu_avg_pct: float
    disk_used_gb: float
    disk_total_gb: float
    disk_pct: float
    net_bytes_sent: int
    net_bytes_recv: int
