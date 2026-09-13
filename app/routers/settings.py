"""Settings router."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import GameplaySettingsSchema
from app.config_manager.parser import SETTING_METADATA
from app.core.atomic_io import atomic_write_ini
from app.core.config import reload_settings
from app.database import UserRecord
from app.routers.deps import engine, perm_server_settings, pipeline, settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["Server Configuration"])


def _write_ini_file_with_fallback(target_path_str: str, serialized_content: str) -> None:
    """Writes serialized INI content to target path with user home fallback on failure."""
    ini_file = Path(target_path_str)
    try:
        ini_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_ini(ini_file, serialized_content)
    except PermissionError as err:
        log.warning("Permission denied writing INI at %s: %s. Using home directory fallback.", ini_file, err)
        fallback_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_ini(fallback_file, serialized_content)
    except OSError as err:
        log.warning("OS error writing INI at %s: %s. Using home directory fallback.", ini_file, err)
        fallback_file = Path.home() / ".palmanager" / "PalWorldSettings.ini"
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_ini(fallback_file, serialized_content)


def stage_settings_for_reboot(serialized_content: str) -> None:
    """Stages serialized INI content to survive in-memory server reboot overwrites."""
    candidates = [
        Path("/var/lib/palmanager/staged_PalWorldSettings.ini"),
        Path.home() / ".palmanager" / "staged_PalWorldSettings.ini",
        Path("/home/steam/.staged_PalWorldSettings.ini"),
    ]
    for cand in candidates:
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_ini(cand, serialized_content, make_backup=False)
        except OSError as err:
            log.debug("Unable to stage settings to candidate %s: %s", cand, err)


@router.get("/api/settings")
async def get_settings_data() -> dict[str, Any]:
    """Returns safe, public-facing server configuration and field metadata.

    Returns:
        dict[str, Any]: Mapping with status, field metadata, and public settings dictionary.

    Raises:
        HTTPException: If reading configuration fails.
    """
    try:
        public_view = pipeline.get_public_view()
        return {"status": "success", "metadata": SETTING_METADATA, "data": public_view}
    except KeyError as e:
        log.error("Missing key fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        log.error("Invalid value fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except RuntimeError as e:
        log.error("Runtime error fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except OSError as e:
        log.error("OS error fetching settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/settings")
async def save_sanitized_settings(
    payload: GameplaySettingsSchema,
    _: UserRecord = Depends(perm_server_settings),
) -> dict[str, Any]:
    """Sanitizes, persists, and Git-commits updated gameplay settings.

    Args:
        payload (GameplaySettingsSchema): Validated gameplay settings input.
        _: Enforces server:settings permission.

    Returns:
        dict[str, Any]: Success status, message, and Git snapshot commit hash.

    Raises:
        HTTPException: If persisting settings fails.
    """
    try:
        sanitized_dict = payload.model_dump(exclude_unset=True)
        serialized_ini = pipeline.merge_and_serialize(sanitized_dict)
        await asyncio.to_thread(_write_ini_file_with_fallback, settings.ini_path, serialized_ini)

        readiness = await engine.check_readiness()
        staged = False
        if readiness.get("ready"):
            engine.stage_settings(serialized_ini)
            stage_settings_for_reboot(serialized_ini)
            staged = True

        reload_settings()
        log.info("Saved and staged settings cleanly to disk.")
        return {
            "status": "success",
            "message": "Settings saved cleanly to disk.",
            "staged": staged,
        }
    except PermissionError as e:
        log.error("Permission denied saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"Permission denied: {e!s}") from e
    except ValueError as e:
        log.error("Validation error saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"Validation error: {e!s}") from e
    except RuntimeError as e:
        log.error("Runtime error saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"Runtime error: {e!s}") from e
    except OSError as e:
        log.error("OS error saving settings: %s", e)
        raise HTTPException(status_code=500, detail=f"OS error: {e!s}") from e
