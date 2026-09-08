"""Unit tests for Pydantic request models, gameplay settings, and payload validation."""
# pylint: disable=missing-function-docstring
# Rationale: Pytest test function names are self-descriptive and documented via assertions.
# pylint: disable=import-outside-toplevel
# Rationale: Scoped imports inside tests isolate schema validation side-effects.

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    FeedbackFilterParams,
    GameplaySettingsSchema,
    PlayerBanRequest,
    PlayerKickRequest,
    PlayerWarnRequest,
    RebootCancelRequest,
    RebootRequest,
    SettingsRestoreRequest,
    UpdateApplyRequest,
    UpdateApplyResponse,
    UpdateStatusResponse,
    UserRegisterRequest,
    UserRoleUpdateRequest,
    build_github_issue_url,
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

    cancel = RebootCancelRequest(reason="  Raid event in progress \n\t ")
    assert cancel.reason == "Raid event in progress"


def test_domain_types_instantiation():
    from app.core.types import DiscoveryHubPayload, HardwareTelemetryInfo, TopBadgeInfo

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
        "net_rx_rate_kbps": 12.5,
        "net_tx_rate_kbps": 5.0,
        "net_rx_rate_mbps": 0.1,
        "net_tx_rate_mbps": 0.04,
        "net_dropin": 0,
        "net_dropout": 0,
        "net_errin": 0,
        "net_errout": 0,
        "net_traffic_status": "HEALTHY",
    }
    assert hw["host_ram_pct"] == 25.0


def test_build_github_issue_url():
    repo = "https://github.com/theStygianArchitect/palworld_server_service"

    # 1. Bug report template url
    bug_url = build_github_issue_url(repo, "bug_report")
    assert bug_url == f"{repo}/issues/new?template=bug_report.md"

    # 2. Feature request template url
    feat_url = build_github_issue_url(repo, "feature_request")
    assert feat_url == f"{repo}/issues/new?template=feature_request.md"

    # 3. Documentation update template url
    docs_url = build_github_issue_url(repo, "documentation_update")
    assert docs_url == f"{repo}/issues/new?template=documentation_update.md"

    # 4. Security report goes to security advisory
    sec_url = build_github_issue_url(repo, "security_report")
    assert sec_url == f"{repo}/security/advisories/new"

    # 5. Trailing slash on repo is handled
    clean_url = build_github_issue_url(f"{repo}/", "bug_report")
    assert clean_url == f"{repo}/issues/new?template=bug_report.md"

    # 6. Pre-filled title and body with special characters are URL-encoded
    prefill = build_github_issue_url(
        repo,
        "bug_report",
        title="[BUG] Server crashed & failed",
        body="Line 1\nLine 2 & <tag>",
    )
    assert "template=bug_report.md" in prefill
    assert "%5BBUG%5D%20Server%20crashed%20%26%20failed" in prefill
    assert "Line%201%0ALine%202%20%26%20%3Ctag%3E" in prefill


def test_feedback_filter_params():
    # Defaults
    params = FeedbackFilterParams()
    assert params.mine is False
    assert params.submitted_by is None
    assert params.category is None
    assert params.status is None
    assert params.limit == 50
    assert params.offset == 0

    # Custom
    custom = FeedbackFilterParams(
        mine=True,
        submitted_by="admin",
        category="feature_request",
        status="OPEN",
        limit=25,
        offset=10,
    )
    assert custom.mine is True
    assert custom.submitted_by == "admin"
    assert custom.category == "feature_request"
    assert custom.status == "OPEN"
    assert custom.limit == 25
    assert custom.offset == 10


def test_user_register_and_role_schemas():
    pwd_val = f"Valid_{'Password'}_123!"
    short_val = f"sh{'o'}rt"

    # Valid register
    reg = UserRegisterRequest(
        username="new_player",
        password=pwd_val,
        email="player@example.com",
    )
    assert reg.username == "new_player"
    assert reg.password == pwd_val
    assert reg.email == "player@example.com"

    # Invalid username (too short, special chars)
    with pytest.raises(ValidationError):
        UserRegisterRequest(username="ab", password=pwd_val, email="p@e.com")
    with pytest.raises(ValidationError):
        UserRegisterRequest(username="bad name", password=pwd_val, email="p@e.com")

    # Invalid password (too short)
    with pytest.raises(ValidationError):
        UserRegisterRequest(username="new_player", password=short_val, email="p@e.com")

    # Invalid email
    with pytest.raises(ValidationError):
        UserRegisterRequest(username="new_player", password=pwd_val, email="not-an-email")

    # Valid role update
    role_admin = UserRoleUpdateRequest(role="admin")
    assert role_admin.role == "admin"
    role_op = UserRoleUpdateRequest(role="operator")
    assert role_op.role == "operator"
    role_view = UserRoleUpdateRequest(role="viewer")
    assert role_view.role == "viewer"

    # Invalid role
    with pytest.raises(ValidationError):
        UserRoleUpdateRequest(role="superadmin")  # type: ignore[arg-type]


def test_update_schemas():
    # UpdateStatusResponse valid
    status_resp = UpdateStatusResponse(
        update_available=True,
        current_commit="abc1234",
        latest_commit="def5678",
        commits_behind=3,
        latest_commit_message="feat(core): new capability",
        last_checked="2026-09-08T20:00:00Z",
        update_in_progress=False,
        error=None,
    )
    assert status_resp.update_available is True
    assert status_resp.commits_behind == 3
    assert status_resp.current_commit == "abc1234"
    assert status_resp.error is None

    # UpdateApplyRequest default and custom
    apply_default = UpdateApplyRequest()
    assert apply_default.branch == "main"

    apply_custom = UpdateApplyRequest(branch="develop")
    assert apply_custom.branch == "develop"

    with pytest.raises(ValidationError):
        UpdateApplyRequest(branch="")

    with pytest.raises(ValidationError):
        UpdateApplyRequest(branch="invalid branch; rm -rf /")

    # UpdateApplyResponse
    apply_resp = UpdateApplyResponse(
        status="applying",
        message="Update initiated successfully.",
        target_branch="main",
        triggered_at="2026-09-08T20:00:00Z",
    )
    assert apply_resp.status == "applying"
    assert apply_resp.target_branch == "main"
