"""Pydantic validation schemas and ingress request models.

Provides strict data validation, string sanitization, and value boundary enforcement
for all REST API requests and gameplay settings modification in compliance with
the Google Style Guide and 3 AM defensive typing principles.
"""

# pylint: disable=too-many-lines
# Rationale: Central schema definitions consolidate data models across game configuration,
# RBAC, metrics, and deployment progression.

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import quote, urlencode

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
        RCONEnabled (bool | None): Whether RCON remote console is enabled (defaults to False).
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

    # 🔒 Remote Administration & Security Defaults (Issue #13)
    RCONEnabled: bool | None = False

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


class UserRegisterRequest(BaseModel):
    """Payload for public self-service user registration.

    Attributes:
        username (str): New user login handle (3-32 characters, alphanumeric/dash/underscore).
        password (str): Secret password (minimum 8 characters).
        email (EmailStr): Valid contact email address.
    """

    username: str = Field(
        ...,
        min_length=3,
        max_length=32,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Alphanumeric username with optional underscores and hyphens",
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Plaintext password meeting minimum 8-character length requirement",
    )
    email: str = Field(
        ...,
        min_length=3,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Valid contact email address for account notification",
    )


class UserRoleUpdateRequest(BaseModel):
    """Payload for administrative role promotion or demotion.

    Attributes:
        role (Literal['admin', 'operator', 'viewer']): Target system role to assign.
    """

    role: Literal["admin", "operator", "viewer"] = Field(
        ...,
        description="Target system role to assign to the user",
    )


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

IssueCategory = Literal[
    "bug_report",
    "feature_request",
    "documentation_update",
    "security_report",
]

TEMPLATE_FILENAMES: dict[IssueCategory, str] = {
    "bug_report": "bug_report.md",
    "feature_request": "feature_request.md",
    "documentation_update": "documentation_update.md",
    "security_report": "security_report.md",
}


class FeedbackFilterParams(BaseModel):
    """Query parameters for filtering recorded feedback submissions.

    Attributes:
        mine (bool): Filter for tickets created by the authenticated user.
        submitted_by (str | None): Optional filter for explicit submitter username.
        category (IssueCategory | None): Filter by issue template category.
        status (Literal["OPEN", "RESOLVED", "CLOSED"] | None): Filter by ticket status.
        limit (int): Max entries to return.
        offset (int): Pagination offset.
    """

    mine: bool = Field(default=False, description="Filter for tickets created by the authenticated user")
    submitted_by: str | None = Field(default=None, description="Optional submitter handle filter")
    category: IssueCategory | None = Field(default=None, description="Filter by issue template category")
    status: Literal["OPEN", "RESOLVED", "CLOSED"] | None = Field(default=None, description="Filter by ticket status")
    limit: int = Field(default=50, ge=1, le=100, description="Max entries to return")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


def build_github_issue_url(
    repo_url: str,
    category: IssueCategory,
    title: str | None = None,
    body: str | None = None,
) -> str:
    """Builds a secure, sanitized deep link to GitHub's issue composer or security advisory.

    Args:
        repo_url: Upstream GitHub repository base URL (e.g. https://github.com/org/repo).
        category: Issue template category identifier.
        title: Optional pre-filled issue title.
        body: Optional pre-filled markdown body content.

    Returns:
        str: Fully qualified, URL-encoded GitHub composer or security advisory URL.
    """
    base = repo_url.rstrip("/")
    if category == "security_report":
        return f"{base}/security/advisories/new"

    template_file = TEMPLATE_FILENAMES.get(category, "bug_report.md")
    query_params: dict[str, str] = {"template": template_file}
    if title:
        query_params["title"] = title
    if body:
        query_params["body"] = body

    return f"{base}/issues/new?{urlencode(query_params, quote_via=quote)}"


# pylint: disable=too-many-instance-attributes
# Rationale: Composite schema covers optional fields across all 4 repository issue templates.
class FeedbackSubmitRequest(BaseModel):
    """Payload for submitting feedback mapped 1:1 to repository issue templates.

    Attributes:
        category (str): Issue category matching GitHub templates.
        title (str): Short summary title.
        description (str): Custom markdown summary overview.
    """

    category: IssueCategory = Field(..., description="Target issue template category")
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


class PostUpdateSummary(BaseModel):
    """Telemetry report of the most recently executed deployment.

    Attributes:
        status (Literal["success", "failed"]): Outcome of last deployment.
        target_branch (str): Target Git branch.
        deployed_version (str | None): Semantic version string deployed (e.g. v0.2.0).
        deployed_commit (str): Full 40-character Git commit hash.
        deployed_commit_short (str): 7-character Git commit hash.
        deployed_at (str): ISO-8601 timestamp of completion.
        duration_seconds (int): Total execution duration in seconds.
        summary (str): Commit message title.
        acknowledged (bool): Whether operator has dismissed the notice.
    """

    status: Literal["success", "failed"] = Field(..., description="Outcome of last deployment")
    target_branch: str = Field(default="main", description="Target Git branch")
    deployed_version: str | None = Field(default=None, description="Semantic version string deployed (e.g. v0.2.0)")
    deployed_commit: str = Field(..., description="Full 40-character Git commit hash")
    deployed_commit_short: str = Field(..., description="Short 7-character Git commit hash")
    deployed_at: str = Field(..., description="ISO-8601 timestamp of completion")
    duration_seconds: int = Field(default=0, ge=0, description="Total execution duration in seconds")
    summary: str = Field(default="", description="Commit message title")
    acknowledged: bool = Field(default=False, description="Whether operator has dismissed the notice")


class UpdateStatusResponse(BaseModel):
    """Telemetry status response for upstream repository updates.

    Attributes:
        update_available (bool): Whether newer commits exist upstream.
        current_version (str): Current application semantic version.
        current_commit (str): Local active commit SHA or identifier.
        latest_commit (str): Remote upstream commit SHA or identifier.
        target_branch (str): Active tracking git branch name.
        commits_behind (int): Number of commits remote is ahead of local.
        latest_commit_message (str): Summary title/message of latest remote commit.
        last_checked (str): ISO-8601 UTC timestamp of last probe.
        update_in_progress (bool): Whether an update deployment is currently executing.
        error (str | None): Detailed error description if probe failed, else None.
        last_update (PostUpdateSummary | None): Metadata of most recent deployment.
    """

    update_available: bool = Field(..., description="Whether upstream changes are available")
    current_version: str = Field(default="0.2.0", description="Current application semantic version")
    current_commit: str = Field(..., description="Active deployed git commit SHA")
    latest_commit: str = Field(..., description="Latest upstream git commit SHA on branch")
    target_branch: str = Field(default="main", description="Active tracking git branch name")
    commits_behind: int = Field(default=0, ge=0, description="Commit count difference")
    latest_commit_message: str = Field(default="", description="Summary of newest upstream commit")
    last_checked: str = Field(..., description="ISO-8601 UTC timestamp of latest probe")
    update_in_progress: bool = Field(default=False, description="Whether deployment lock is active")
    error: str | None = Field(default=None, description="Diagnostic error message if probe failed")
    last_update: PostUpdateSummary | None = Field(
        default=None, description="Metadata of most recent deployment if completed"
    )


class UpdateApplyRequest(BaseModel):
    """Request payload to trigger upstream software deployment.

    Attributes:
        branch (str): Target git branch to pull and deploy.
    """

    branch: str = Field(
        default="main",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.-]+$",
        description="Target git branch to pull and deploy",
    )


class UpdateApplyResponse(BaseModel):
    """Response payload returned upon initiating software deployment.

    Attributes:
        status (str): Outcome status indicator.
        message (str): Explanatory message for operator.
        target_branch (str): Branch targeted for deployment.
        triggered_at (str): ISO-8601 UTC timestamp of invocation.
    """

    status: str = Field(default="applying", description="Execution lifecycle status")
    message: str = Field(..., description="Status description")
    target_branch: str = Field(..., description="Branch being deployed")
    triggered_at: str = Field(..., description="ISO-8601 UTC timestamp when triggered")


class DeploymentStepInfo(BaseModel):
    """Step execution status model.

    Attributes:
        index (int): 1-indexed step number.
        name (str): Step title/description.
        status (Literal["pending", "running", "completed", "failed"]): Execution state.
    """

    index: int = Field(..., description="1-indexed step number")
    name: str = Field(..., description="Step title/description")
    status: Literal["pending", "running", "completed", "failed"] = Field(
        default="pending", description="Current step execution status"
    )


class DeploymentProgressResponse(BaseModel):
    """Real-time progression telemetry DTO.

    Attributes:
        operation (Literal["portal_update", "tls_certificate", "server_restart", "none"]): Operation type.
        active (bool): Whether a deployment is actively running.
        current_step (int): 1-based active step index (0 if idle).
        total_steps (int): Total steps in sequence.
        step_name (str): Name of the currently running or pending step.
        percentage (int): Overall completion percent (0-100).
        elapsed_seconds (int): Seconds elapsed since start.
        estimated_remaining_seconds (int | None): Estimated time to completion in seconds.
        steps (list[DeploymentStepInfo]): Ordered step sequence.
        log_tail (list[str]): Last N lines from deployment log.
        last_update (PostUpdateSummary | None): Metadata of most recent update if completed.
    """

    operation: Literal["portal_update", "tls_certificate", "server_restart", "none"] = Field(
        default="none", description="Active deployment operation type"
    )
    active: bool = Field(default=False, description="Whether a deployment is actively running")
    current_step: int = Field(default=0, ge=0, description="1-based active step index (0 if idle)")
    total_steps: int = Field(default=5, ge=1, description="Total steps in sequence")
    step_name: str = Field(default="", description="Name of the currently running or pending step")
    percentage: int = Field(default=0, ge=0, le=100, description="Overall completion percent (0-100)")
    elapsed_seconds: int = Field(default=0, ge=0, description="Seconds elapsed since start")
    estimated_remaining_seconds: int | None = Field(
        default=None, description="Dynamic estimated time to completion in seconds"
    )
    steps: list[DeploymentStepInfo] = Field(default_factory=list, description="Ordered step sequence")
    log_tail: list[str] = Field(default_factory=list, description="Last 25 lines from deployment log")
    last_update: PostUpdateSummary | None = Field(
        default=None, description="Metadata of most recent update if completed"
    )


class PostUpdateAcknowledgeResponse(BaseModel):
    """Acknowledgement response dismissing post-update banner.

    Attributes:
        status (str): Outcome status indicator.
        acknowledged_at (str): ISO-8601 UTC timestamp of acknowledgement.
    """

    status: str = Field(default="success", description="Outcome status")
    acknowledged_at: str = Field(..., description="ISO-8601 UTC timestamp of acknowledgement")


# =========================================================================
# 8. System Bootstrap & Initial Setup Schemas
# =========================================================================


class BootstrapCredentialsResponse(BaseModel):
    """Response payload for initial system bootstrap credential presentation.

    Returned by GET /api/auth/bootstrap-credentials when the system is in
    first-spin setup mode and the initial admin credentials have not yet
    been acknowledged. Once acknowledged via POST /api/auth/ack-bootstrap,
    this endpoint permanently returns HTTP 404.

    Attributes:
        is_pending (bool): True if bootstrap acknowledgment is still outstanding.
        username (str | None): The bootstrapped admin account username.
        password (str | None): The plaintext generated admin password (ephemeral).
        message (str): Operator guidance and security warning text.
    """

    is_pending: bool = Field(..., description="True if bootstrap acknowledgment is still outstanding")
    username: str | None = Field(default=None, description="Bootstrapped admin account username")
    password: str | None = Field(default=None, description="Plaintext generated admin password (ephemeral)")
    message: str = Field(..., description="Operator guidance and security warning text")


class BootstrapAckResponse(BaseModel):
    """Response payload confirming bootstrap credential acknowledgment.

    Returned by POST /api/auth/ack-bootstrap. After this response is issued,
    the ephemeral plaintext password is zeroed from memory and GET /api/auth/bootstrap-credentials
    permanently returns HTTP 404.

    Attributes:
        status (str): Operation status ('success').
        message (str): Confirmation that credentials have been locked.
    """

    status: str = Field(..., description="Operation status ('success')")
    message: str = Field(..., description="Confirmation that credentials have been locked and wiped from memory")


class ShutdownRequest(BaseModel):
    """Request payload for the graceful server shutdown endpoint.

    Validates the countdown duration and broadcast message before enqueueing
    the shutdown sequence. The sequence broadcasts countdown messages, forces
    a world save, then invokes systemd to restart the palworld.service unit.

    Attributes:
        seconds (int): Countdown duration in seconds before the server restarts.
            Must be between 30 and 3600 (1 hour). Defaults to 300 (5 minutes).
        message (str): Broadcast message shown to players during the countdown.
            Maximum 80 characters. Spaces will be replaced with underscores to
            work around the Palworld RCON broadcast space-truncation bug (the
            /v1/api/announce REST endpoint does not have this limitation).
    """

    seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Countdown duration in seconds (30-3600). Defaults to 300.",
    )
    message: str = Field(
        default="Server restarting",
        max_length=80,
        description="Broadcast message to display to players. Max 80 characters.",
    )


class ShutdownResponse(BaseModel):
    """Response payload confirming a graceful shutdown has been scheduled.

    Returned by POST /api/server/shutdown. The shutdown executes in a FastAPI
    background task; this response is returned immediately after the task is
    enqueued, not after the server has restarted.

    Attributes:
        status (str): Always 'scheduled'.
        countdown_seconds (int): The countdown duration that was accepted.
        message (str): The broadcast message that will be shown to players.
    """

    status: str = Field(..., description="Always 'scheduled'")
    countdown_seconds: int = Field(..., description="Accepted countdown duration in seconds")
    message: str = Field(..., description="Broadcast message shown to players during countdown")


class SaveResponse(BaseModel):
    """Response payload confirming a manual world save was triggered.

    Returned by POST /api/server/save. The save is executed synchronously
    against the Palworld REST API; this response is returned only after the
    engine has confirmed the save completed.

    Attributes:
        status (str): 'ok' on success.
        triggered_by (str): Username of the operator who requested the save.
        timestamp (str): ISO 8601 UTC timestamp of when the save was triggered.
    """

    status: str = Field(..., description="'ok' on success")
    triggered_by: str = Field(..., description="Username of the operator who triggered the save")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of the save trigger")


# =========================================================================
# 11. TLS / SSL Infrastructure Schemas
# =========================================================================


class TLSCertificateInfo(BaseModel):
    """Parsed X.509 certificate metadata.

    Attributes:
        subject (str): Common Name or subject DN of the certificate.
        issuer (str): Certificate authority that issued the certificate.
        valid_from (str): ISO 8601 UTC timestamp when certificate becomes valid.
        expires_at (str): ISO 8601 UTC timestamp when certificate expires.
        days_remaining (int): Integer days remaining until certificate expiration.
        is_expired (bool): True if current time is past expiration date.
        san_list (list[str]): Subject Alternative Names registered to certificate.
    """

    subject: str = Field(..., description="Certificate subject Common Name or DN")
    issuer: str = Field(..., description="Certificate issuing authority")
    valid_from: str = Field(..., description="ISO 8601 UTC start of validity period")
    expires_at: str = Field(..., description="ISO 8601 UTC expiration timestamp")
    days_remaining: int = Field(..., description="Days remaining before certificate expires")
    is_expired: bool = Field(..., description="Whether certificate is currently expired")
    san_list: list[str] = Field(default_factory=list, description="Subject Alternative Names")


class TLSStatusResponse(BaseModel):
    """Runtime TLS/HTTPS status payload for dashboard and REST consumers.

    Attributes:
        enabled (bool): Whether HTTPS / TLS encryption is actively running.
        scheme (Literal['http', 'https']): Active URL scheme.
        domain (str): Configured domain hostname.
        port (int): Web management plane listening port.
        certificate (TLSCertificateInfo | None): Parsed certificate metadata if available.
        cert_path (str | None): Filesystem path to certificate chain if loaded.
        auto_renew_active (bool): Whether background systemd renewal timer is registered.
        warning (str | None): Optional warning message if certificate is nearing expiration or invalid.
        canonical_url (str): Canonical public HTTPS access URL.
    """

    enabled: bool = Field(..., description="Whether HTTPS encryption is actively running")
    scheme: Literal["http", "https"] = Field(..., description="Active protocol scheme")
    domain: str = Field(..., description="Configured server domain hostname")
    port: int = Field(..., description="Active web server port")
    certificate: TLSCertificateInfo | None = Field(default=None, description="Active certificate metadata")
    cert_path: str | None = Field(default=None, description="Filesystem path to certificate file")
    auto_renew_active: bool = Field(default=False, description="Whether automated renewal is active")
    warning: str | None = Field(default=None, description="Operational warning or configuration issue")
    canonical_url: str = Field(
        default="https://thestygianarchitect.duckdns.org:8080",
        description="Canonical public HTTPS access URL",
    )


class TLSRenewRequest(BaseModel):
    """Request payload for triggering an on-demand TLS certificate renewal.

    Attributes:
        force (bool): When True, passes --force-renewal to certbot to renew even if not nearing expiration.
    """

    force: bool = Field(
        default=False,
        description="Whether to force immediate renewal even if the certificate is not yet near expiration.",
    )


class TLSRenewResponse(BaseModel):
    """Response returned upon triggering an automated certificate renewal.

    Attributes:
        status (Literal['queued', 'success', 'failed', 'skipped']): Execution status.
        message (str): Informational outcome message.
        triggered_at (str): ISO 8601 UTC timestamp when renewal was triggered.
    """

    status: Literal["queued", "success", "failed", "skipped"] = Field(
        ..., description="Execution status of the renewal trigger"
    )
    message: str = Field(..., description="Informational outcome message")
    triggered_at: str = Field(..., description="ISO 8601 timestamp of execution")


# =========================================================================
# 10. Semantic Versioning & Changelog Engine Schemas
# =========================================================================


class SystemVersionResponse(BaseModel):
    """Application semantic version response schema.

    Attributes:
        version (str): Application semantic version string.
        canonical_url (str): Canonical public HTTPS portal access URL.
    """

    version: str = Field(default="0.2.0", description="Application semantic version string")
    canonical_url: str = Field(
        default="https://thestygianarchitect.duckdns.org:8080",
        description="Canonical public HTTPS portal access URL",
    )


class ChangelogCategoryItem(BaseModel):
    """Represents a category of changes and associated bullet items.

    Attributes:
        category (str): Category label (e.g., Added, Changed, Fixed, Planned).
        items (list[str]): Bulleted descriptions under this category.
    """

    category: str = Field(..., description="Changelog section category name")
    items: list[str] = Field(default_factory=list, description="Bulleted change items under this category")


class ChangelogRelease(BaseModel):
    """Represents a released version entry in the changelog.

    Attributes:
        version (str): Semantic version string (e.g. 0.2.0).
        date (str | None): Release date in ISO-8601 format (YYYY-MM-DD), or None.
        categories (list[ChangelogCategoryItem]): Structured change categories for this release.
        raw_body (str): Raw unparsed markdown section body.
    """

    version: str = Field(..., description="Semantic version string")
    date: str | None = Field(default=None, description="Release date in YYYY-MM-DD format")
    categories: list[ChangelogCategoryItem] = Field(default_factory=list, description="Structured change categories")
    raw_body: str = Field(default="", description="Raw markdown body of release section")


class ChangelogResponse(BaseModel):
    """Changelog response containing active version, historical releases, and unreleased items.

    Attributes:
        current_version (str): Currently deployed application semantic version.
        releases (list[ChangelogRelease]): Chronologically ordered release entries.
        unreleased (list[ChangelogCategoryItem]): Pending items under unreleased section.
    """

    current_version: str = Field(..., description="Current application semantic version")
    releases: list[ChangelogRelease] = Field(default_factory=list, description="Historical release entries")
    unreleased: list[ChangelogCategoryItem] = Field(
        default_factory=list, description="Pending changes in unreleased section"
    )
