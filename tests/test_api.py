from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_api_index_html_route():
    response = client.get("/")
    assert response.status_code == 200
    assert "Palworld" in response.text
    assert "html" in response.headers.get("content-type", "")


def test_api_settings_get_route():
    response = client.get("/api/settings")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "success"
    assert "metadata" in json_data
    assert "data" in json_data


def test_api_settings_post_route():
    payload = {
        "ExpRate": 2.5,
        "PalCaptureRate": 1.5,
        "ServerName": "Updated Server",
    }
    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_community_tracker_route():
    response = client.get("/api/tracker/community")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "success"
    assert "data" in json_data
    assert "discovery_hub" in json_data["data"]
    assert "a2s_telemetry" in json_data["data"]
    assert "security_matrix" in json_data["data"]


def test_api_reboot_with_custom_message_route():
    payload = {
        "countdown_seconds": 60,
        "trigger_steam_update": False,
        "custom_message": "Scheduled memory purge and restart.",
    }
    with patch("app.main.engine.execute_countdown_and_reboot", new_callable=AsyncMock) as mock_reboot:
        response = client.post("/api/service/reboot", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_reboot.assert_called_once_with(60, False, "", "Scheduled memory purge and restart.")


def test_api_moderation_routes():
    with patch("app.main.engine.kick_player", new_callable=AsyncMock, return_value=True) as mock_kick:
        res = client.post("/api/players/kick", json={"player_id": "1001", "message": "Spamming"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_kick.assert_called_once_with("1001", "Spamming")

    with patch("app.main.engine.ban_player", new_callable=AsyncMock, return_value=True) as mock_ban:
        res = client.post("/api/players/ban", json={"player_id": "1002", "message": "Cheating"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_ban.assert_called_once_with("1002", "Cheating")

    with patch("app.main.engine.send_broadcast", new_callable=AsyncMock, return_value=True) as mock_warn:
        res = client.post("/api/players/warn", json={"message": "Maintenance starting in 15m"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_warn.assert_called_once_with("[ADMIN NOTICE] Maintenance starting in 15m", mirror_discord=True)


def test_api_backups_routes():
    # Commits list
    commits_res = client.get("/api/backups/commits")
    assert commits_res.status_code == 200
    assert commits_res.json()["status"] == "success"

    # Diff route
    diff_res = client.get("/api/backups/diff/nonexistent")
    assert diff_res.status_code == 200
    assert diff_res.json()["status"] == "success"

    # Restore route
    restore_res = client.post("/api/backups/restore/nonexistent")
    assert restore_res.status_code == 200
    assert restore_res.json()["status"] == "success"
