import pytest
from pydantic import ValidationError

from app.schemas import (
    GameplaySettingsSchema,
    PlayerBanRequest,
    PlayerKickRequest,
    PlayerWarnRequest,
    RebootRequest,
    SettingsRestoreRequest,
)


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


def test_isolated_request_schemas():
    reboot = RebootRequest(countdown_seconds=120, custom_message="Server upgrade")
    assert reboot.countdown_seconds == 120
    assert reboot.custom_message == "Server upgrade"
    assert reboot.trigger_steam_update is False

    kick = PlayerKickRequest(player_id="steam_12345", message="Rule violation")
    assert kick.player_id == "steam_12345"
    assert kick.message == "Rule violation"

    ban = PlayerBanRequest(player_id="steam_99999", message="Exploiting")
    assert ban.player_id == "steam_99999"

    warn = PlayerWarnRequest(message="Server event in 10 minutes!")
    assert warn.message == "Server event in 10 minutes!"

    restore = SettingsRestoreRequest(commit_hash="a1b2c3d")
    assert restore.commit_hash == "a1b2c3d"


def test_domain_types_instantiation():
    from app.types import DiscoveryHubPayload, HardwareTelemetryInfo, TopBadgeInfo

    badge: TopBadgeInfo = {
        "label": "Community Listed",
        "style": "bg-emerald-900",
        "dot": "bg-emerald-400",
    }
    assert badge["label"] == "Community Listed"

    hub: DiscoveryHubPayload = {
        "log_scraper": {
            "registered": True,
            "session_id": "test_sess",
            "first_seen": "2026-08-30",
            "last_line": "Created public lobby session",
            "status_label": "READY",
            "status_color": "emerald",
            "crossplay_platforms": "Steam",
        },
        "pocketpair_master": {
            "listed": True,
            "server_id": "pp_123",
            "name": "Test Server",
            "version": "v0.3.0",
            "status_label": "LISTED",
            "status_color": "emerald",
        },
        "network_matrix": {
            "public_ip": "1.2.3.4",
            "dns_ip": "1.2.3.4",
            "domain": "test.duckdns.org",
            "is_aligned": True,
            "lan_ip": "192.168.1.100",
            "public_port": 8211,
            "direct_connect_addr": "test.duckdns.org:8211",
            "lan_connect_addr": "192.168.1.100:8211",
        },
    }
    assert hub["network_matrix"]["is_aligned"] is True

    hw: HardwareTelemetryInfo = {
        "host_ram_used_gb": 8.0,
        "host_ram_total_gb": 32.0,
        "host_ram_pct": 25.0,
        "cgroup_ram_used_gb": 4.0,
        "cgroup_limit_gb": 14.0,
        "cgroup_ram_pct": 28.5,
        "swap_used_gb": 0.0,
        "swap_total_gb": 8.0,
        "swap_pct": 0.0,
        "cpu_cores": [10.0, 15.0],
        "cpu_avg_pct": 12.5,
        "disk_used_gb": 50.0,
        "disk_total_gb": 500.0,
        "disk_pct": 10.0,
        "net_bytes_sent": 1000,
        "net_bytes_recv": 2000,
    }
    assert hw["host_ram_pct"] == 25.0
