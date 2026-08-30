import re

from pydantic import BaseModel, Field, field_validator


class GameplaySettingsSchema(BaseModel):
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
    def sanitize_strings(cls, v: str) -> str:
        if v is None:
            return ""
        return re.sub(r'[\r\n\t"]', "", v).strip()

    @field_validator("DeathPenalty")
    @classmethod
    def validate_death_penalty(cls, v: str) -> str:
        allowed = {"None", "Item", "ItemAndEquipment", "All"}
        return v if v in allowed else "None"
