"""Unit tests verifying settings staging and persistence across server reboot."""

# pylint: disable=protected-access
# Rationale: Direct validation of private staging caches during reboot simulation.

from pathlib import Path

import pytest

from app.engine.service import EngineConfig, EnginePaths, PalEngine
from app.main import stage_settings_for_reboot

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_maintenance_script_has_staged_configuration_step() -> None:
    """Verifies that palworld-maintenance.sh checks and synchronizes staged INI config."""
    script = REPO_ROOT / "scripts" / "palworld-maintenance.sh"
    assert script.is_file(), f"Maintenance script not found: {script}"

    content = script.read_text(encoding="utf-8")
    assert "[4/4] Checking for staged world configuration..." in content
    assert "staged_PalWorldSettings.ini" in content
    assert "TARGET_INI=" in content
    assert "Synchronizing to $TARGET_INI" in content


def test_engine_stage_settings_and_apply(tmp_path: Path) -> None:
    """Verifies that PalworldEngine stages settings and writes to target path during maintenance."""
    ini_path = tmp_path / "PalWorldSettings.ini"
    paths = EnginePaths(
        ini_path=ini_path,
        service_name="palworld.service",
        update_flag=tmp_path / "update.flag",
        lock_file=tmp_path / "server.lock",
    )
    config = EngineConfig(paths=paths)
    engine = PalEngine(config=config)

    test_ini_content = "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerPlayerMaxNum=16)\n"
    engine.stage_settings(test_ini_content)
    assert engine._staged_ini == test_ini_content

    engine._apply_staged_configuration()
    assert engine._staged_ini is None
    assert ini_path.is_file()
    assert ini_path.read_text(encoding="utf-8") == test_ini_content


def test_stage_settings_for_reboot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that stage_settings_for_reboot writes drop-in files to candidate directories."""
    mock_home = tmp_path / "home"
    mock_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: mock_home)

    test_content = "OptionSettings=(Difficulty=Normal)"
    stage_settings_for_reboot(test_content)

    fallback_file = mock_home / ".palmanager" / "staged_PalWorldSettings.ini"
    assert fallback_file.is_file()
    assert fallback_file.read_text(encoding="utf-8") == test_content
