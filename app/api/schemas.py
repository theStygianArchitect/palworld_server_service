"""Pydantic validation schemas and ingress request models.

Provides strict data validation, string sanitization, and value boundary enforcement
for all REST API requests and gameplay settings modification in compliance with
the Google Style Guide and 3 AM defensive typing principles.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# =========================================================================
# 1. Gameplay Settings Validation Schema
# =========================================================================


class GameplaySettingsSchema(BaseModel):
    """Pydantic validation schema for Palworld INI gameplay configuration values.

    Validates, sanitizes, and bounds numeric multipliers, difficulty thresholds,
    and text attributes parsed from or serialized to PalWorldSettings.ini.

    Attributes:
        ServerName (str | None): Publicly visible dedicated server name.
        ServerDescription (str | None): Extended description displayed in browser.
        Region (str | None): Geographical server region designation.
        DayTimeSpeedRate (float | None): Multiplier for daytime progression speed.
        NightTimeSpeedRate (float | None): Multiplier for nighttime progression speed.
        ExpRate (float | None): Player and Pal experience gain rate multiplier.
        PalCaptureRate (float | None): Pal sphere capture probability multiplier.
        PalSpawnNumRate (float | None): Wild Pal density and spawn count multiplier.
        PalDamageRateAttack (float | None): Pal outgoing attack damage multiplier.
        PalDamageRateDefense (float | None): Pal incoming defense damage multiplier.
        PlayerDamageRateAttack (float | None): Player outgoing attack damage multiplier.
        PlayerDamageRateDefense (float | None): Player incoming defense damage multiplier.
        PlayerStomachDecreaceRate (float | None): Player hunger rate multiplier.
        PlayerStaminaDecreaceRate (float | None): Player stamina drain rate multiplier.
        PalStomachDecreaceRate (float | None): Pal hunger rate multiplier.
        PalStaminaDecreaceRate (float | None): Pal stamina drain rate multiplier.
        CollectionDropRate (float | None): Material gathering yield rate multiplier.
        CollectionObjectHpRate (float | None): Durability HP for resource nodes.
        CollectionObjectRespawnSpeedRate (float | None): Node respawn rate multiplier.
        EnemyDropItemRate (float | None): Defeated enemy loot drop rate multiplier.
        DeathPenalty (str | None): Penalty on player death ('None', 'Item', 'ItemAndEquipment', 'All').
        bEnablePlayerToPlayerDamage (bool | None): Whether PvP damage is permitted.
        bEnableFriendlyFire (bool | None): Whether guild/friendly fire is permitted.
        bEnableInvaderEnemy (bool | None): Whether base raid events are active.
        bActiveUNKO (bool | None): Whether creature waste generation is enabled.
        BaseCampMaxNum (int | None): Maximum base camps allowed across the guild.
        BaseCampWorkerMaxNum (int | None): Maximum assigned worker Pals per base.
        GuildPlayerMaxNum (int | None): Maximum members allowed per guild.
        PalEggDefaultHatchingTime (float | None): Base incubation duration in hours.
        WorkSpeedRate (float | None): Pal workstation task completion speed rate.
        CrossplayPlatforms (str | None): Platform crossplay compatibility list.
        bIsMultiplay (bool | None): Whether multiplayer mode is enabled.
        bShowPlayerList (bool | None): Whether active player list is publicly visible.
        bIsShowJoinLeftMessage (bool | None): Whether join/leave notices display.
        SupplyDropSpan (int | None): Interval in minutes between meteorite supply drops.
    """

    # 🌐 Crossplay & Matchmaking
    ServerName: str | None = Field(default="The Cool Kids Palworld Server", max_length=128)
    ServerDescription: str | None = Field(default="", max_length=256)
    Region: str | None = Field(default="", max_length=32)
    CrossplayPlatforms: str | None = Field(default="(Steam,Xbox,PS5,Mac)")
    AllowConnectPlatform: str | None = Field(default="(Steam,Xbox,PS5,Mac)")
    bIsMultiplay: bool | None = False
    bShowPlayerList: bool | None = False
    bIsShowJoinLeftMessage: bool | None = True
    ServerPlayerMaxNum: int | None = Field(default=32, ge=1, le=32)
    CoopPlayerMaxNum: int | None = Field(default=4, ge=1, le=4)
    bUseBackupSaveData: bool | None = True

    # ⚡ Progression & Leveling
    Difficulty: str | None = Field(default="None")
    ExpRate: float | None = Field(default=1.0, ge=0.1, le=20.0)
    PalCaptureRate: float | None = Field(default=1.0, ge=0.5, le=5.0)
    PalEggDefaultHatchingTime: float | None = Field(default=0.0, ge=0.0, le=240.0)
    WorkSpeedRate: float | None = Field(default=1.0, ge=0.1, le=10.0)
    DayTimeSpeedRate: float | None = Field(default=1.0, ge=0.1, le=10.0)
    NightTimeSpeedRate: float | None = Field(default=1.0, ge=0.1, le=10.0)

    # 🛡️ Stamina & Survival
    PlayerStaminaDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)
    PalStaminaDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)
    PlayerStomachDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)
    PalStomachDecreaceRate: float | None = Field(default=1.0, ge=0.1, le=5.0)
    PlayerAutoHPRegeneRate: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PlayerAutoHPRegeneRateInSleep: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PalAutoHPRegeneRate: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PalAutoHPRegeneRateInSleep: float | None = Field(default=1.0, ge=0.1, le=10.0)
    bEnableNonLoginPenalty: bool | None = True
    bEnableFastTravel: bool | None = True
    bIsStartLocationSelectByMap: bool | None = True
    bExistPlayerAfterLogout: bool | None = False

    # ⚔️ Combat & Death Rules
    DeathPenalty: str | None = Field(default="None")
    bEnableInvaderEnemy: bool | None = True
    bEnablePlayerToPlayerDamage: bool | None = False
    bEnableFriendlyFire: bool | None = False
    bEnableDefenseOtherGuildPlayer: bool | None = False
    bInvisibleOtherGuildBaseCampAreaFX: bool | None = False
    PlayerDamageRateAttack: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PlayerDamageRateDefense: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PalDamageRateAttack: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PalDamageRateDefense: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PalDamageRateToPlayer: float | None = Field(default=1.0, ge=0.1, le=10.0)
    PalDamageRateFromPlayer: float | None = Field(default=1.0, ge=0.1, le=10.0)
    bEnableAimAssistPad: bool | None = True
    bEnableAimAssistKeyboard: bool | None = False

    # 🏰 Base Camps & Guild Scaling
    BaseCampMaxNum: int | None = Field(default=128, ge=1, le=256)
    BaseCampMaxNumInGuild: int | None = Field(default=4, ge=1, le=20)
    BaseCampWorkerMaxNum: int | None = Field(default=20, ge=1, le=50)
    GuildPlayerMaxNum: int | None = Field(default=20, ge=1, le=100)
    MaxBuildingLimitNum: int | None = Field(default=0, ge=0, le=10000)
    BuildObjectDamageRate: float | None = Field(default=1.0, ge=0.1, le=10.0)
    BuildObjectDeteriorationDamageRate: float | None = Field(default=1.0, ge=0.0, le=10.0)
    AutoResetGuildNoOnlinePlayers: bool | None = False
    AutoResetGuildTimeNoOnlinePlayers: float | None = Field(default=72.0, ge=0.0, le=720.0)

    # 📦 Gathering, Drops & Spawns
    CollectionDropRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    CollectionObjectHpRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    CollectionObjectRespawnSpeedRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    EnemyDropItemRate: float | None = Field(default=1.0, ge=0.5, le=10.0)
    PalSpawnNumRate: float | None = Field(default=1.0, ge=0.5, le=3.0)
    DropItemMaxNum: int | None = Field(default=3000, ge=0, le=5000)
    DropItemMaxNum_UNKO: int | None = Field(default=100, ge=0, le=500)
    DropItemAliveMaxHours: float | None = Field(default=1.0, ge=0.0, le=24.0)
    bActiveUNKO: bool | None = False
    SupplyDropSpan: int | None = Field(default=180, ge=0, le=1000)

    @field_validator("ServerName", "ServerDescription", "Region")
    @classmethod
    def sanitize_strings(cls, v: str | None) -> str:
        """Sanitizes text fields by removing quotes, newlines, and carriage returns.

        Args:
            v (str | None): Raw input string.

        Returns:
            str: Cleaned and stripped text string.
        """
        if v is None:
            return ""
        return re.sub(r'[\r\n\t"]', "", v).strip()

    @field_validator("DeathPenalty")
    @classmethod
    def validate_death_penalty(cls, v: str | None) -> str:
        """Validates that death penalty setting is one of the supported game modes.

        Args:
            v (str | None): Selected death penalty identifier.

        Returns:
            str: Validated death penalty string, or 'None' as safe default.
        """
        allowed = {"None", "Item", "ItemAndEquipment", "All"}
        if v in allowed:
            return str(v)
        return "None"


# =========================================================================
# 2. Ingress Request Payloads
# =========================================================================


class SettingsUpdateRequest(BaseModel):
    """Payload for updating INI configuration with gameplay settings dictionary.

    Attributes:
        settings (dict[str, Any]): Key-value mapping of updated gameplay settings.
    """

    settings: dict[str, Any] = Field(..., description="Key-value mapping of updated gameplay settings")


class RebootRequest(BaseModel):
    """Payload for scheduling a graceful or immediate server restart.

    Attributes:
        countdown_seconds (int): Countdown duration in seconds before restart.
        trigger_steam_update (bool): Whether to invoke a SteamCMD update check.
        update_version_tag (str): Target update release version tag.
        custom_message (str): Optional custom broadcast announcement text.
        settings (dict[str, Any] | None): Optional settings to persist prior to reboot.
    """

    countdown_seconds: int = Field(default=60, ge=0, le=3600, description="Countdown duration in seconds")
    trigger_steam_update: bool = Field(default=False, description="Whether to trigger a SteamCMD update flag")
    update_version_tag: str = Field(default="", max_length=64, description="Target update release version tag")
    custom_message: str = Field(default="", max_length=256, description="Optional custom broadcast notice")
    settings: dict[str, Any] | None = Field(
        default=None, description="Optional updated settings to persist prior to reboot"
    )


class RebootCancelRequest(BaseModel):
    """Payload for cancelling an active server reboot countdown sequence.

    Attributes:
        reason (str): Optional administrative explanation for cancelling the restart.
    """

    reason: str = Field(
        default="",
        max_length=200,
        description="Optional administrative explanation for cancelling the reboot",
    )

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, value: str) -> str:
        """Strips control characters and normalizes whitespace in the cancellation reason."""
        return re.sub(r"[\r\n\t]+", " ", value).strip()


class PlayerKickRequest(BaseModel):
    """Payload for administrative player kick action.

    Attributes:
        player_id (str): Unique target player identifier string.
        message (str): Reason text displayed to the kicked player.
    """

    player_id: str = Field(..., min_length=1, max_length=64, description="Unique target player ID")
    message: str = Field(default="Kicked by administrator", max_length=128, description="Notice sent to player")


class PlayerBanRequest(BaseModel):
    """Payload for administrative player ban action.

    Attributes:
        player_id (str): Unique target player identifier string.
        message (str): Reason text recorded in ban list.
    """

    player_id: str = Field(..., min_length=1, max_length=64, description="Unique target player ID")
    message: str = Field(default="Banned by administrator", max_length=128, description="Reason for ban")


class PlayerWarnRequest(BaseModel):
    """Payload for administrative in-game HUD broadcast notice.

    Attributes:
        message (str): Announcement text displayed across the HUD and Discord.
    """

    message: str = Field(..., min_length=1, max_length=256, description="Announcement text shown to all players")


class SettingsRestoreRequest(BaseModel):
    """Payload for restoring configuration from a Git snapshot commit hash.

    Attributes:
        commit_hash (str): Target Git commit hash identifier to roll back to.
    """

    commit_hash: str = Field(
        ...,
        min_length=4,
        max_length=64,
        pattern=r"^[a-fA-F0-9]{4,64}$",
        description="Target Git commit hash to roll back to",
    )


# =========================================================================
# 3. User Authentication & RBAC Schemas
# =========================================================================


class UserLoginRequest(BaseModel):
    """Payload for authenticating a user via username and password.

    Attributes:
        username (str): Registered login handle.
        password (str): Plaintext secret password.
    """

    username: str = Field(..., min_length=1, max_length=64, description="User login handle")
    password: str = Field(..., min_length=1, max_length=128, description="User secret password")


class UserLoginResponse(BaseModel):
    """Response payload returned upon successful user authentication.

    Attributes:
        status (str): Outcome indicator string ('success').
        token (str): Cryptographically signed session token.
        username (str): Authenticated user handle.
        role (str): Primary assigned role ('admin', 'operator', 'viewer').
        permissions (list[str]): List of granted fine-grained permission tokens.
    """

    status: str = Field(default="success")
    token: str = Field(..., description="Signed session token string")
    username: str = Field(..., description="Authenticated user handle")
    role: str = Field(..., description="Assigned role identifier")
    permissions: list[str] = Field(default_factory=list, description="Granted permission tokens")


class UserCreateRequest(BaseModel):
    """Payload for creating a new user account.

    Attributes:
        username (str): New user login handle.
        password (str): Plaintext initial password.
        email (str): Contact email address.
        role (str): Initial role ('admin', 'operator', 'viewer').
        permissions (list[str] | None): Optional list of custom granular permissions.
    """

    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Alphanumeric username",
    )
    password: str = Field(..., min_length=4, max_length=128, description="Initial secret password")
    email: str = Field(default="", max_length=128, description="Optional contact email")
    role: Literal["admin", "operator", "viewer"] = Field(default="viewer", description="Assigned role")
    permissions: list[str] | None = Field(default=None, description="Optional custom permissions list")


class UserUpdateRequest(BaseModel):
    """Payload for updating an existing user record.

    Attributes:
        email (str | None): Updated email address.
        role (Literal['admin', 'operator', 'viewer'] | None): Updated primary role.
        is_active (bool | None): Updated active state.
        password (str | None): Updated password.
        permissions (list[str] | None): Updated granular permissions.
    """

    email: str | None = Field(default=None, max_length=128)
    role: Literal["admin", "operator", "viewer"] | None = Field(default=None)
    is_active: bool | None = Field(default=None)
    password: str | None = Field(default=None, min_length=4, max_length=128)
    permissions: list[str] | None = Field(default=None)


class UserResponse(BaseModel):
    """Public representation of a registered user record.

    Attributes:
        id (int): Primary key unique identifier.
        username (str): Unique login handle.
        email (str): Contact email.
        role (str): Primary assigned role.
        is_active (bool): Whether account is active.
        created_at (str): ISO-8601 UTC creation timestamp.
        last_login (str | None): ISO-8601 UTC last login timestamp.
        permissions (list[str]): List of granted fine-grained permissions.
    """

    id: int = Field(..., description="Unique primary key identifier")
    username: str = Field(..., description="Unique user handle")
    email: str = Field(default="", description="Contact email address")
    role: str = Field(..., description="Assigned primary role")
    is_active: bool = Field(default=True, description="Account active status")
    created_at: str = Field(..., description="ISO-8601 creation timestamp")
    last_login: str | None = Field(default=None, description="Last successful login timestamp")
    permissions: list[str] = Field(default_factory=list, description="Granted permission tokens")


class LoginAuditResponse(BaseModel):
    """Representation of an immutable login audit trail entry.

    Attributes:
        id (int): Primary key unique identifier.
        username (str): Attempted username.
        timestamp (str): ISO-8601 UTC timestamp of attempt.
        ip_address (str): Remote client IP address.
        user_agent (str): Inbound client User-Agent string.
        status (str): Attempt outcome ('SUCCESS' or 'FAILED').
        failure_reason (str): Diagnostic explanation if failed.
    """

    id: int = Field(..., description="Unique primary key identifier")
    username: str = Field(..., description="Attempted user handle")
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp of attempt")
    ip_address: str = Field(..., description="Client IP address")
    user_agent: str = Field(..., description="Client User-Agent string")
    status: str = Field(..., description="Attempt status (SUCCESS or FAILED)")
    failure_reason: str = Field(default="", description="Diagnostic failure explanation")


# =========================================================================
# 4. Template-Driven Feedback & Issue Submission Schemas
# =========================================================================


# pylint: disable=too-many-instance-attributes
# Rationale: Composite schema covers optional fields across all 4 repository issue templates.
class FeedbackSubmitRequest(BaseModel):
    """Payload for submitting feedback mapped 1:1 to repository issue templates.

    Attributes:
        category (str): Issue category matching GitHub templates.
        title (str): Short summary title.
        description (str): Custom markdown summary overview.
    """

    category: Literal["bug_report", "feature_request", "documentation_update", "security_report"] = Field(
        ..., description="Target issue template category"
    )
    title: str = Field(..., min_length=3, max_length=200, description="Summary title of the issue")
    description: str = Field(default="", max_length=5000, description="Optional custom markdown overview")

    # 🐛 Bug Report Fields (.github/ISSUE_TEMPLATE/bug_report.md)
    expected_behavior: str | None = Field(default=None, max_length=2000)
    current_behavior: str | None = Field(default=None, max_length=2000)
    steps_to_reproduce: str | None = Field(default=None, max_length=2000)
    host_environment: str | None = Field(default=None, max_length=1000)
    diagnostic_logs: str | None = Field(default=None, max_length=5000)
    proposed_solution: str | None = Field(default=None, max_length=2000)

    # 🚀 Feature Request Fields (.github/ISSUE_TEMPLATE/feature_request.md)
    feature_proposal: str | None = Field(default=None, max_length=2000)
    problem_user_story: str | None = Field(default=None, max_length=2000)
    twelve_factor_considerations: str | None = Field(default=None, max_length=2000)
    alternatives_considered: str | None = Field(default=None, max_length=2000)

    # 📝 Documentation Update Fields (.github/ISSUE_TEMPLATE/documentation_update.md)
    documentation_area: str | None = Field(default=None, max_length=500)
    motivation_missing_context: str | None = Field(default=None, max_length=2000)
    proposed_content: str | None = Field(default=None, max_length=5000)

    # 🛡️ Security Report Fields (.github/ISSUE_TEMPLATE/security_report.md)
    vulnerability_summary: str | None = Field(default=None, max_length=1000)
    affected_files_lines: str | None = Field(default=None, max_length=500)
    cwe_identifier: str | None = Field(default=None, max_length=100)
    severity: Literal["Critical", "High", "Medium", "Low"] | None = Field(default=None)
    poc_reproduction: str | None = Field(default=None, max_length=3000)
    recommended_remediation: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    """Response payload representing a recorded feedback submission.

    Attributes:
        id (int): Primary key unique identifier.
        category (str): Issue category.
        title (str): Summary title.
        description (str): Rendered markdown description.
        metadata (dict[str, Any]): Template-specific metadata dictionary.
        submitted_by (str): Submitter username or client handle.
        status (str): Lifecycle status ('OPEN', 'RESOLVED', 'CLOSED').
        github_issue_number (int | None): Linked GitHub issue number.
        created_at (str): ISO-8601 UTC creation timestamp.
    """

    id: int
    category: str
    title: str
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    submitted_by: str
    status: str
    github_issue_number: int | None = None
    created_at: str


# =========================================================================
# 5. Time-Series Historical Metrics & Summary Schemas
# =========================================================================


# pylint: disable=too-many-instance-attributes
# Rationale: Historical time-series bucket response models aggregate telemetry metrics for graphing.
class MetricBucketResponse(BaseModel):
    """Downsampled aggregated metrics data point for a time bucket.

    Attributes:
        bucket_timestamp (str): Start of the time bucket.
        avg_fps (float): Average server tick rate.
        min_fps (float): Minimum server tick rate.
        max_fps (float): Maximum server tick rate.
        avg_frame_time_ms (float): Average frame time in ms.
        avg_players (float): Average concurrent players.
        max_players (int): Peak concurrent players.
        avg_cpu_pct (float): Average host CPU %.
        max_cpu_pct (float): Peak host CPU %.
        avg_ram_pct (float): Average host RAM %.
        max_ram_pct (float): Peak host RAM %.
        sample_count (int): Sample count in bucket.
    """

    bucket_timestamp: str = Field(..., description="ISO-8601 bucket start timestamp")
    avg_fps: float = Field(..., description="Average tick rate in FPS")
    min_fps: float = Field(..., description="Minimum tick rate in FPS")
    max_fps: float = Field(..., description="Maximum tick rate in FPS")
    avg_frame_time_ms: float = Field(..., description="Average frame time in milliseconds")
    avg_players: float = Field(..., description="Average concurrent active players")
    max_players: int = Field(..., description="Peak concurrent active players")
    avg_cpu_pct: float = Field(..., description="Average host CPU utilization percentage")
    max_cpu_pct: float = Field(..., description="Peak host CPU utilization percentage")
    avg_ram_pct: float = Field(..., description="Average host RAM utilization percentage")
    max_ram_pct: float = Field(..., description="Peak host RAM utilization percentage")
    sample_count: int = Field(..., description="Number of samples aggregated in bucket")


class MetricHistoryResponse(BaseModel):
    """Historical telemetry series response for charting.

    Attributes:
        window (str): Requested time window ('1h', '24h', '7d', '30d').
        total_buckets (int): Number of time buckets returned.
        buckets (list[MetricBucketResponse]): Time-series bucket items.
    """

    window: str = Field(..., description="Requested time range filter")
    total_buckets: int = Field(..., description="Total aggregation bucket count")
    buckets: list[MetricBucketResponse] = Field(default_factory=list, description="Downsampled bucket points")


# pylint: disable=too-many-instance-attributes
# Rationale: Summary response delivers 30-day KPIs spanning performance, player activity, and resources.
class MetricSummaryResponse(BaseModel):
    """Statistical overview of metrics across a rolling 30-day window.

    Attributes:
        total_samples (int): Total recorded metric data points.
        peak_players (int): Peak concurrent players over 30 days.
        avg_players (float): Average concurrent players over 30 days.
        lowest_fps (float): Minimum server FPS observed.
        avg_fps (float): Average server FPS observed.
        peak_cpu_pct (float): Highest CPU utilization percentage reached.
        avg_cpu_pct (float): Average CPU utilization percentage.
        peak_ram_pct (float): Highest RAM utilization percentage reached.
        avg_ram_pct (float): Average RAM utilization percentage.
        window_start (str): Timestamp of earliest metric sample in window.
        window_end (str): Timestamp of latest metric sample in window.
    """

    total_samples: int = Field(..., description="Total samples recorded")
    peak_players: int = Field(..., description="Maximum concurrent players recorded")
    avg_players: float = Field(..., description="Mean concurrent players")
    lowest_fps: float = Field(..., description="Minimum tick rate observed")
    avg_fps: float = Field(..., description="Mean tick rate observed")
    peak_cpu_pct: float = Field(..., description="Peak host CPU percentage")
    avg_cpu_pct: float = Field(..., description="Mean host CPU percentage")
    peak_ram_pct: float = Field(..., description="Peak host RAM percentage")
    avg_ram_pct: float = Field(..., description="Mean host RAM percentage")
    buffered_samples: int = Field(default=0, description="Current snapshots held in-memory before batch flush")
    window_start: str = Field(..., description="Earliest timestamp in 30-day window")
    window_end: str = Field(..., description="Latest timestamp in 30-day window")


class MetricPruneResponse(BaseModel):
    """Response payload for metrics retention pruning operation.

    Attributes:
        status (str): Outcome status indicator.
        pruned_records (int): Number of records deleted older than retention limit.
        retention_days (int): Configured retention window in days.
    """

    status: str = Field(default="success", description="Outcome status")
    pruned_records: int = Field(..., description="Total deleted records count")
    retention_days: int = Field(..., description="Retention duration applied in days")


class MetricFlushResponse(BaseModel):
    """Response payload for manual metrics buffer flush operation.

    Attributes:
        status (str): Outcome status indicator.
        flushed_snapshots (int): Number of snapshots flushed from memory to disk.
        buffered_remaining (int): Number of snapshots remaining in memory buffer.
    """

    status: str = Field(default="success", description="Outcome status")
    flushed_snapshots: int = Field(..., description="Snapshots written to disk")
    buffered_remaining: int = Field(default=0, description="Snapshots remaining in memory buffer")
