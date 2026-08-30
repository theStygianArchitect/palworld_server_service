import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class GameplaySettingsSchema(BaseModel):
    ServerName: Optional[str] = Field(default="The Cool Kids Palworld Server", max_length=128)
    ServerDescription: Optional[str] = Field(default="", max_length=256)
    Region: Optional[str] = Field(default="", max_length=32)

    DayTimeSpeedRate: Optional[float] = Field(default=1.0, ge=0.1, le=10.0)
    NightTimeSpeedRate: Optional[float] = Field(default=1.0, ge=0.1, le=10.0)
    ExpRate: Optional[float] = Field(default=1.0, ge=0.1, le=20.0)
    PalCaptureRate: Optional[float] = Field(default=1.0, ge=0.5, le=5.0)
    PalSpawnNumRate: Optional[float] = Field(default=1.0, ge=0.5, le=3.0)
    PalDamageRateAttack: Optional[float] = Field(default=1.0, ge=0.1, le=10.0)
    PalDamageRateDefense: Optional[float] = Field(default=1.0, ge=0.1, le=10.0)
    PlayerDamageRateAttack: Optional[float] = Field(default=1.0, ge=0.1, le=10.0)
    PlayerDamageRateDefense: Optional[float] = Field(default=1.0, ge=0.1, le=10.0)
    PlayerStomachDecreaceRate: Optional[float] = Field(default=1.0, ge=0.1, le=5.0)
    PlayerStaminaDecreaceRate: Optional[float] = Field(default=1.0, ge=0.1, le=5.0)
    PalStomachDecreaceRate: Optional[float] = Field(default=1.0, ge=0.1, le=5.0)
    PalStaminaDecreaceRate: Optional[float] = Field(default=1.0, ge=0.1, le=5.0)

    CollectionDropRate: Optional[float] = Field(default=1.0, ge=0.5, le=10.0)
    CollectionObjectHpRate: Optional[float] = Field(default=1.0, ge=0.5, le=10.0)
    CollectionObjectRespawnSpeedRate: Optional[float] = Field(default=1.0, ge=0.5, le=10.0)
    EnemyDropItemRate: Optional[float] = Field(default=1.0, ge=0.5, le=10.0)
    DeathPenalty: Optional[str] = Field(default="None")
    bEnablePlayerToPlayerDamage: Optional[bool] = False
    bEnableFriendlyFire: Optional[bool] = False
    bEnableInvaderEnemy: Optional[bool] = True
    bActiveUNKO: Optional[bool] = False

    BaseCampMaxNum: Optional[int] = Field(default=128, ge=1, le=256)
    BaseCampWorkerMaxNum: Optional[int] = Field(default=20, ge=1, le=50)
    GuildPlayerMaxNum: Optional[int] = Field(default=20, ge=1, le=100)
    PalEggDefaultHatchingTime: Optional[float] = Field(default=0.0, ge=0.0, le=240.0)
    WorkSpeedRate: Optional[float] = Field(default=1.0, ge=0.1, le=10.0)

    CrossplayPlatforms: Optional[str] = Field(default="(Steam,Xbox,PS5,Mac)")
    bIsMultiplay: Optional[bool] = False
    bShowPlayerList: Optional[bool] = False
    bIsShowJoinLeftMessage: Optional[bool] = True
    SupplyDropSpan: Optional[int] = Field(default=180, ge=0, le=1000)

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
