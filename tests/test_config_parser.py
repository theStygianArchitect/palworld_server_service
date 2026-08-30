import os
import tempfile
from app.config_parser import parse_ini_file, serialize_ini_settings
from app.config_pipeline import ConfigPipeline, PROTECTED_ADMIN_KEYS

SAMPLE_INI_CONTENT = """[/Script/Pal.PalGameWorldSettings]
OptionSettings=(Difficulty=None,ExpRate=1.500000,PalCaptureRate=1.200000,PalSpawnNumRate=1.000000,DeathPenalty="None",bEnablePlayerToPlayerDamage=False,bEnableInvaderEnemy=True,ServerName="The Cool Kids Palworld Server",ServerDescription="Welcome to our server",AdminPassword="SecretPassword123",ServerPassword="",PublicPort=8211,RCONEnabled=True,RCONPort=25575,RESTAPIEnabled=True,RESTAPIPort=8212,CrossplayPlatforms=(Steam,Xbox,PS5,Mac))
"""


def test_parse_ini_file():
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(SAMPLE_INI_CONTENT)
        temp_path = f.name

    try:
        data = parse_ini_file(temp_path)
        assert data["ExpRate"] == 1.5
        assert data["PalCaptureRate"] == 1.2
        assert data["DeathPenalty"] == "None"
        assert data["bEnablePlayerToPlayerDamage"] is False
        assert data["bEnableInvaderEnemy"] is True
        assert data["ServerName"] == "The Cool Kids Palworld Server"
        assert data["AdminPassword"] == "SecretPassword123"
        assert data["CrossplayPlatforms"] == "(Steam,Xbox,PS5,Mac)"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_serialize_ini_settings():
    settings = {
        "ExpRate": 2.0,
        "bEnableInvaderEnemy": True,
        "ServerName": "Test Server",
        "CrossplayPlatforms": "(Steam,Xbox)",
    }
    serialized = serialize_ini_settings(settings)
    assert "[/Script/Pal.PalGameWorldSettings]" in serialized
    assert "ExpRate=2.000000" in serialized
    assert "bEnableInvaderEnemy=True" in serialized
    assert 'ServerName="Test Server"' in serialized
    assert "CrossplayPlatforms=(Steam,Xbox)" in serialized


def test_config_pipeline_protects_admin_keys():
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(SAMPLE_INI_CONTENT)
        temp_path = f.name

    try:
        pipeline = ConfigPipeline(temp_path)
        public_view, protected_view = pipeline.read_to_json()

        for key in PROTECTED_ADMIN_KEYS:
            assert key not in public_view
            if key in {"AdminPassword", "PublicPort", "RCONEnabled", "RCONPort"}:
                assert key in protected_view

        # Modify public settings and merge
        incoming = {"ExpRate": 3.0, "ServerName": "New Name", "AdminPassword": "HackedPassword"}
        merged_ini = pipeline.merge_and_serialize(incoming)

        assert "ExpRate=3.000000" in merged_ini
        assert 'ServerName="New Name"' in merged_ini
        assert 'AdminPassword="SecretPassword123"' in merged_ini
        assert "HackedPassword" not in merged_ini
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
