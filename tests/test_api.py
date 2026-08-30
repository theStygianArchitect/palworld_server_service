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
    assert "network_matrix" in json_data["data"]
    assert "source_of_truth_battlemetrics" in json_data["data"]


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
