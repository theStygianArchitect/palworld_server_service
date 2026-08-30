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
    "CrossplayPlatforms": {
        "category": "🌐 Crossplay & Community Matchmaking",
        "type": "string",
        "label": "Allowed Crossplay Platforms",
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
        "label": "Egg Incubation Time (0.0 = Instant)",
        "min": 0.0,
        "max": 72.0,
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
    "PlayerDamageRateAttack": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Player Attack Multiplier",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PlayerDamageRateDefense": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Player Defense Multiplier (Lower = Less DMG)",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalDamageRateAttack": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Pal Attack Multiplier",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "PalDamageRateDefense": {
        "category": "⚔️ Combat & Death Rules",
        "type": "float",
        "label": "Pal Defense Multiplier (Lower = Less DMG)",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
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
    "BaseCampMaxNum": {
        "category": "🏰 Base Camps & Guild Scaling",
        "type": "int",
        "label": "Max World Base Camps",
        "min": 1,
        "max": 256,
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
        "label": "Structure Decay Outside Base",
        "min": 0.0,
        "max": 10.0,
        "step": 0.1,
    },
    "CollectionDropRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Harvesting / Node Drop Rate",
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
        "label": "Pal Spawn Density",
        "min": 0.5,
        "max": 3.0,
        "step": 0.1,
    },
    "SupplyDropSpan": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "int",
        "label": "Supply Drop Interval (Minutes)",
        "min": 0,
        "max": 600,
        "step": 10,
    },
    "DayTimeSpeedRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Day Time Speed Rate",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    "NightTimeSpeedRate": {
        "category": "📦 Gathering, Drops & Spawns",
        "type": "float",
        "label": "Night Time Speed Rate",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
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
