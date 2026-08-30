"""Palworld INI Configuration Parser, Serializer, and Field Metadata Registry.

Provides robust parsing of Unreal Engine OptionSettings formatted INI files,
type coercion, serialization, and UI field presentation metadata.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .logger import log

SETTING_METADATA: dict[str, dict[str, Any]] = {
    # 🌐 Crossplay & Matchmaking
    "CrossplayPlatforms": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "string",
        "label": "Allowed Crossplay Platforms",
    },
    "AllowConnectPlatform": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "string",
        "label": "Connect Platforms String",
    },
    "bIsMultiplay": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "bool",
        "label": "Community Multiplayer Mode",
    },
    "bShowPlayerList": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "bool",
        "label": "Show In-Game Player List",
    },
    "bIsShowJoinLeftMessage": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "bool",
        "label": "Show Join/Leave Notices",
    },
    "ServerPlayerMaxNum": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "int",
        "label": "Max Server Player Capacity",
        "min": 1,
        "max": 32,
        "step": 1,
    },
    "CoopPlayerMaxNum": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "int",
        "label": "Max Co-Op Party Size",
        "min": 1,
        "max": 4,
        "step": 1,
    },
    "bUseBackupSaveData": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "bool",
        "label": "Automatic World Backup Save Data",
    },
    "ServerName": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "string",
        "label": "Server Display Name",
    },
    "ServerDescription": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "string",
        "label": "Server Description",
    },
    "Region": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "string",
        "label": "Server Region",
    },
    # ⚡ Progression & Leveling
    "Difficulty": {
        "category": "⚡ Progression & Leveling",
        "type": "select",
        "label": "World Difficulty Preset",
        "options": ["None", "Casual", "Normal", "Hard"],
    },
    "ExpRate": {
        "category": "⚡ Progression & Leveling",
        "type": "float",
        "label": "Player & Pal EXP Multiplier",
        "min": 0.1,
        "max": 20.0,
        "step": 0.1,
    },
    "PalCaptureRate": {
        "category": "⚡ Progression & Leveling",
        "type": "float",
        "label": "Pal Capture Rate Multiplier",
        "min": 0.5,
        "max": 5.0,
        "step": 0.1,
    },
    "PalEggDefaultHatchingTime": {
        "category": "⚡ Progression & Leveling",
        "type": "float",
        "label": "Egg Incubation Time (Hours; 0.0 = Instant)",
        "min": 0.0,
        "max": 240.0,
        "step": 0.5,
    },
    "WorkSpeedRate": {
        "category": "⚡ Progression & Leveling",
        "type": "float",
        "label": "Player Work Speed Rate",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "DayTimeSpeedRate": {
        "category": "⚡ Progression & Leveling",
        "type": "float",
        "label": "Day Time Speed Rate",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "NightTimeSpeedRate": {
        "category": "⚡ Progression & Leveling",
        "type": "float",
        "label": "Night Time Speed Rate",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    # 🛡️ Stamina & Survival
    "PlayerStaminaDecreaceRate": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Player Stamina Drain Rate",
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
    },
    "PalStaminaDecreaceRate": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Mount / Pal Stamina Drain Rate",
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
    },
    "PlayerStomachDecreaceRate": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Player Hunger Drain Rate",
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
    },
    "PalStomachDecreaceRate": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Pal Hunger Drain Rate",
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
    },
    "PlayerAutoHPRegeneRate": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Player Passive HP Regen",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PlayerAutoHPRegeneRateInSleep": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Player Sleeping HP Regen",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalAutoHPRegeneRate": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Pal Passive HP Regen",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalAutoHPRegeneRateInSleep": {
        "category": "🛡️ Stamina & Survival",
        "type": "float",
        "label": "Pal Box / Sleep HP Regen",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "bEnableNonLoginPenalty": {
        "category": "🛡️ Stamina & Survival",
        "type": "bool",
        "label": "Non-Login Player Penalty",
    },
    "bEnableFastTravel": {
        "category": "🛡️ Stamina & Survival",
        "type": "bool",
        "label": "Fast Travel Statues & Points",
    },
    "bIsStartLocationSelectByMap": {
        "category": "🛡️ Stamina & Survival",
        "type": "bool",
        "label": "Respawn / Start Location Selection on Map",
    },
    "bExistPlayerAfterLogout": {
        "category": "🛡️ Stamina & Survival",
        "type": "bool",
        "label": "Player Body Persists in World After Logout",
    },
    # ⚔️ Combat & Death Rules
    "DeathPenalty": {
        "category": "⚔️ Combat & Death Rules",
        "type": "select",
        "label": "Death Penalty (None = No Drop)",
        "options": ["None", "Item", "ItemAndEquipment", "All"],
    },
    "bEnableInvaderEnemy": {
        "category": "⚔️ Combat & Death Rules",
        "type": "bool",
        "label": "Base Raids / Invader Attacks",
    },
    "bEnablePlayerToPlayerDamage": {
        "category": "⚔️ Combat & Death Rules",
        "type": "bool",
        "label": "PvP Player-to-Player Damage",
    },
    "bEnableFriendlyFire": {
        "category": "⚔️ Combat & Death Rules",
        "type": "bool",
        "label": "Guild Friendly Fire",
    },
    "bEnableDefenseOtherGuildPlayer": {
        "category": "⚔️ Combat & Death Rules",
        "type": "bool",
        "label": "Defend Against Other Guild Players",
    },
    "bInvisibleOtherGuildBaseCampAreaFX": {
        "category": "⚔️ Combat & Death Rules",
        "type": "bool",
        "label": "Hide Other Guild Base Perimeter FX",
    },
    "PlayerDamageRateAttack": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Player Outgoing Attack Multiplier",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PlayerDamageRateDefense": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Player Incoming Damage Multiplier",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalDamageRateAttack": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Pal Outgoing Attack Multiplier",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalDamageRateDefense": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Pal Incoming Damage Multiplier",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalDamageRateToPlayer": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Wild Pal Damage Dealt to Players",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalDamageRateFromPlayer": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Player Damage Dealt to Wild Pals",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "bEnableAimAssistPad": {
        "category": "⚔️ Combat & Death Rules",
        "type": "bool",
        "label": "Controller Aim Assist",
    },
    "bEnableAimAssistKeyboard": {
        "category": "⚔️ Combat & Death Rules",
        "type": "bool",
        "label": "Keyboard/Mouse Aim Assist",
    },
    # 🏰 Base Camps & Guild Scaling
    "BaseCampMaxNum": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "int",
        "label": "Max World Base Camps",
        "min": 1,
        "max": 256,
        "step": 1,
    },
    "BaseCampMaxNumInGuild": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "int",
        "label": "Max Base Camps Per Guild",
        "min": 1,
        "max": 20,
        "step": 1,
    },
    "BaseCampWorkerMaxNum": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "int",
        "label": "Max Assigned Pals Per Base",
        "min": 1,
        "max": 50,
        "step": 1,
    },
    "GuildPlayerMaxNum": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "int",
        "label": "Max Players Per Guild",
        "min": 1,
        "max": 100,
        "step": 1,
    },
    "MaxBuildingLimitNum": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "int",
        "label": "Max Total World Building Elements (0 = Unlimited)",
        "min": 0,
        "max": 10000,
        "step": 100,
    },
    "BuildObjectDamageRate": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "float",
        "label": "Damage to Structures Multiplier",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "BuildObjectDeteriorationDamageRate": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "float",
        "label": "Structure Decay Outside Base (0 = Disabled)",
        "min": 0.0,
        "max": 10.0,
        "step": 0.1,
    },
    "AutoResetGuildNoOnlinePlayers": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "bool",
        "label": "Auto-Disband Inactive Guilds",
    },
    "AutoResetGuildTimeNoOnlinePlayers": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "float",
        "label": "Inactive Guild Reset Timer (Hours)",
        "min": 0.0,
        "max": 720.0,
        "step": 1.0,
    },
    # 📦 Gathering, Drops & Spawns
    "CollectionDropRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Harvesting / Node Drop Rate",
        "min": 0.5,
        "max": 10.0,
        "step": 0.1,
    },
    "CollectionObjectHpRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Resource Node Durability (HP)",
        "min": 0.5,
        "max": 10.0,
        "step": 0.1,
    },
    "CollectionObjectRespawnSpeedRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Resource Node Respawn Speed Rate",
        "min": 0.5,
        "max": 10.0,
        "step": 0.1,
    },
    "EnemyDropItemRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Enemy Pal Item Drop Rate",
        "min": 0.5,
        "max": 10.0,
        "step": 0.1,
    },
    "PalSpawnNumRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Pal Spawn Density Multiplier",
        "min": 0.5,
        "max": 3.0,
        "step": 0.1,
    },
    "DropItemMaxNum": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "int",
        "label": "Max Dropped Items in World",
        "min": 0,
        "max": 5000,
        "step": 100,
    },
    "DropItemMaxNum_UNKO": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "int",
        "label": "Max Dropped Waste / Dung in World",
        "min": 0,
        "max": 500,
        "step": 10,
    },
    "DropItemAliveMaxHours": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Dropped Item Despawn Timer (Hours)",
        "min": 0.0,
        "max": 24.0,
        "step": 0.5,
    },
    "bActiveUNKO": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "bool",
        "label": "Creature Waste / Dung Generation (UNKO)",
    },
    "SupplyDropSpan": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "int",
        "label": "Supply Drop Interval (Minutes; 0 = Disabled)",
        "min": 0,
        "max": 1000,
        "step": 10,
    },
}


def parse_ini_file(path: str | Path) -> dict[str, Any]:
    """Parses a Palworld PalGameWorldSettings INI file into a Python dictionary.

    Reads the OptionSettings payload, extracts key-value pairs, and converts values
    to appropriate Python types (bool, int, float, str).

    Args:
        path (str | Path): Path to the PalWorldSettings.ini file on disk.

    Returns:
        dict[str, Any]: Extracted dictionary of key-value gameplay settings.
    """
    file_path = Path(path)
    try:
        content = file_path.read_text(encoding="utf-8")
    except FileNotFoundError as err:
        log.debug("INI file not found at %s: %s", file_path, err)
        return {}
    except PermissionError as err:
        log.warning("Permission denied reading INI file at %s: %s", file_path, err)
        return {}
    except OSError as err:
        log.warning("OS error reading INI file at %s: %s", file_path, err)
        return {}

    match = re.search(r"OptionSettings=\((.*)\)", content, re.DOTALL)
    if not match:
        return {}

    raw_str = match.group(1).strip()
    tokens: dict[str, Any] = {}
    pattern = re.compile(r'(\w+)=(?:"([^"]*)"|(\([^)]*\))|([^,]+))')
    for m in pattern.finditer(raw_str):
        k = m.group(1)
        if m.group(2) is not None:
            tokens[k] = m.group(2)
        elif m.group(3) is not None:
            tokens[k] = m.group(3)
        elif m.group(4) is not None:
            raw_v = m.group(4).strip()
            if raw_v == "True":
                tokens[k] = True
            elif raw_v == "False":
                tokens[k] = False
            else:
                try:
                    tokens[k] = int(raw_v) if "." not in raw_v else float(raw_v)
                except ValueError as err:
                    log.debug("Token %s='%s' not numeric (%s); keeping as raw string", k, raw_v, err)
                    tokens[k] = raw_v
    return tokens


def serialize_ini_settings(settings: dict[str, Any]) -> str:
    """Serializes a dictionary of gameplay settings into PalWorldSettings.ini format.

    Args:
        settings (dict[str, Any]): Dictionary of configuration keys and values.

    Returns:
        str: Fully formatted INI file content with [/Script/Pal.PalGameWorldSettings] header.
    """
    pairs: list[str] = []
    for k, v in settings.items():
        if isinstance(v, bool):
            pairs.append(f"{k}={'True' if v else 'False'}")
        elif isinstance(v, float):
            pairs.append(f"{k}={v:.6f}")
        elif isinstance(v, int):
            pairs.append(f"{k}={v}")
        elif isinstance(v, str):
            if v.startswith("(") and v.endswith(")"):
                pairs.append(f"{k}={v}")
            else:
                pairs.append(f'{k}="{v}"')
        else:
            pairs.append(f'{k}="{v}"')

    inner = ",".join(pairs)
    return f"[/Script/Pal.PalGameWorldSettings]\nOptionSettings=({inner})\n"
