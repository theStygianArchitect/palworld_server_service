import pytest
from pydantic import ValidationError

from app.schemas import GameplaySettingsSchema


def test_gameplay_settings_schema_defaults_and_types():
    schema = GameplaySettingsSchema()
    assert schema.ExpRate == 1.0
    assert schema.PalCaptureRate == 1.0
    assert schema.bEnableInvaderEnemy is True
    assert schema.DeathPenalty == "None"


def test_gameplay_settings_schema_sanitization():
    payload = {
        "ExpRate": 15.0,
        "PalCaptureRate": 2.5,
        "ServerName": '  "My Awesome Server" \n\t  ',
        "ServerDescription": "Hello\nWorld\r\t",
        "DeathPenalty": "NonExistentPenalty",
    }
    schema = GameplaySettingsSchema(**payload)
    assert schema.ExpRate == 15.0
    assert schema.PalCaptureRate == 2.5
    assert schema.ServerName == "My Awesome Server"
    assert schema.ServerDescription == "HelloWorld"
    assert schema.DeathPenalty == "None"


def test_gameplay_settings_schema_validation_bounds():
    # Out of bounds should raise ValidationError
    with pytest.raises(ValidationError):
        GameplaySettingsSchema(ExpRate=50.0)

    with pytest.raises(ValidationError):
        GameplaySettingsSchema(PalCaptureRate=-1.0)


def test_gameplay_settings_schema_valid_death_penalty():
    for val in ["None", "Item", "ItemAndEquipment", "All"]:
        s = GameplaySettingsSchema(DeathPenalty=val)
        assert s.DeathPenalty == val
