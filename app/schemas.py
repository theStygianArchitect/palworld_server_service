"""Pydantic validation schemas and ingress request models.

Provides strict data validation, string sanitization, and value boundary enforcement
for all REST API requests and gameplay settings modification in compliance with
the Google Style Guide and 3 AM defensive typing principles.
"""

from __future__ import annotations

import re
from typing import Any

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
