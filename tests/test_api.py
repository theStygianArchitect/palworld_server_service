from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provides an isolated FastAPI TestClient fixture."""
    return TestClient(app)


def test_api_health_and_ready_routes(client: TestClient):
    # 1. Test /health liveness endpoint
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "healthy"
    assert res_health.json()["service"] == "palworld-web-manager"
    assert "timestamp" in res_health.json()

    # 2. Test /ready when engine is offline (503)
    with patch(
        "app.main.engine.check_readiness",
        AsyncMock(return_value={"ready": False, "version": None, "server_name": "PalServer"}),
    ):
        res_not_ready = client.get("/ready")
        assert res_not_ready.status_code == 503

    # 3. Test /ready when engine is online (200)
    with patch(
        "app.main.engine.check_readiness",
        AsyncMock(return_value={"ready": True, "version": "v0.3.5", "server_name": "Live Server"}),
    ):
        res_ready = client.get("/ready")
        assert res_ready.status_code == 200
        assert res_ready.json()["status"] == "ready"
        assert res_ready.json()["version"] == "v0.3.5"


def test_api_index_html_route(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Palworld" in response.text
    assert "html" in response.headers.get("content-type", "")


def test_api_settings_get_route(client: TestClient):
    response = client.get("/api/settings")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "success"
    assert "metadata" in json_data
    assert "data" in json_data


def test_api_settings_post_route(client: TestClient):
    payload = {
        "ExpRate": 2.5,
        "PalCaptureRate": 1.5,
        "ServerName": "Updated Server",
    }
    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_community_tracker_route(client: TestClient):
    response = client.get("/api/tracker/community")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "success"
    assert "data" in json_data
    assert "discovery_hub" in json_data["data"]
    assert "a2s_telemetry" in json_data["data"]
    assert "security_matrix" in json_data["data"]


def test_api_reboot_with_custom_message_route(client: TestClient):
    payload = {
        "countdown_seconds": 2,
        "trigger_steam_update": False,
        "custom_message": "Scheduled memory purge and restart.",
    }
    with patch("app.main.engine.execute_countdown_and_reboot", new_callable=AsyncMock) as mock_reboot:
        response = client.post("/api/service/reboot", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_reboot.assert_called_once_with(2, False, "", "Scheduled memory purge and restart.")


def test_api_moderation_routes(client: TestClient):
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


def test_api_backups_routes(client: TestClient):
    # Commits list
    commits_res = client.get("/api/backups/commits")
    assert commits_res.status_code == 200
    assert commits_res.json()["status"] == "success"

    # Invalid non-hex hash returns 400 Bad Request
    invalid_diff = client.get("/api/backups/diff/invalid_hash_string")
    assert invalid_diff.status_code == 400

    invalid_restore = client.post("/api/backups/restore/invalid_hash_string")
    assert invalid_restore.status_code == 400

    # Valid hex hash diff route
    with patch("app.main.git_mgr.get_diff", return_value="--- a\n+++ b"):
        diff_res = client.get("/api/backups/diff/abcd1234ef01")
        assert diff_res.status_code == 200
        assert diff_res.json()["status"] == "success"
        assert "diff" in diff_res.json()

    # Valid hex hash restore route
    with patch("app.main.git_mgr.restore_commit", return_value=True):
        with patch("app.main.reload_settings"):
            restore_res = client.post("/api/backups/restore/abcd1234ef01")
            assert restore_res.status_code == 200
            assert restore_res.json()["status"] == "success"


def test_api_logs_routes(client: TestClient):
    with patch(
        "app.main.engine.tracker.read_server_logs",
        return_value={
            "status": "success",
            "source": "/PalServer/Pal/Saved/Logs/Pal.log",
            "total_lines_scanned": 50,
            "returned_lines": 2,
            "lines": [
                "[2026.08.30-10.15.00:123] LogPalServer: Engine ready",
                "[2026.08.30-10.15.01:456] Created public lobby session",
            ],
        },
    ):
        res = client.get("/api/logs?tail=50&level=ALL")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert len(data["lines"]) == 2
        assert "Engine ready" in data["lines"][0]

    with patch(
        "app.main.engine.tracker.read_server_logs",
        return_value={
            "status": "success",
            "source": "/PalServer/Pal/Saved/Logs/Pal.log",
            "total_lines_scanned": 10,
            "returned_lines": 1,
            "lines": ["[2026.08.30-10.15.00:123] LogPalServer: Engine ready"],
        },
    ):
        download_res = client.get("/api/logs/download")
        assert download_res.status_code == 200
        assert "attachment" in download_res.headers.get("content-disposition", "")
        assert "Engine ready" in download_res.text
