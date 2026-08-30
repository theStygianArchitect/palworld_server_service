import os
import tempfile
from unittest.mock import patch, MagicMock
import pytest
from app.tracker import CommunityTracker


def test_tracker_player_ledger():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = os.path.join(tmpdir, "players.json")
        os.environ["PLAYER_LEDGER_PATH"] = ledger_path

        tracker = CommunityTracker("TestServer", "test.duckdns.org")

        live_players = [
            {
                "playerId": "1001",
                "userId": "steam_1001",
                "name": "PalMaster99",
                "level": 45,
                "ping": 25.5,
                "location_x": 1200.5,
                "location_y": -450.2,
            }
        ]

        # First update
        matrix1 = tracker.update_and_get_players(live_players)
        assert matrix1["active_count"] == 1
        assert matrix1["total_registered"] == 1
        assert matrix1["active_players"][0]["name"] == "PalMaster99"
        assert matrix1["active_players"][0]["status"] == "ONLINE"

        # Second update: player goes offline
        matrix2 = tracker.update_and_get_players([])
        assert matrix2["active_count"] == 0
        assert matrix2["total_registered"] == 1
        assert matrix2["offline_players"][0]["playerId"] == "1001"
        assert matrix2["offline_players"][0]["status"] == "OFFLINE"


def test_tracker_hardware_telemetry():
    tracker = CommunityTracker()
    hw = tracker.get_hardware_telemetry()
    assert "host_ram_used_gb" in hw
    assert "host_ram_total_gb" in hw
    assert "cpu_cores" in hw
    assert "cgroup_limit_gb" in hw


@pytest.mark.asyncio
async def test_tracker_combined_telemetry():
    tracker = CommunityTracker("TestServer", "test.duckdns.org")
    telemetry = await tracker.get_combined_telemetry(is_multiplay=True, host_ip="10.0.0.1", public_port=8211)

    assert "top_badge" in telemetry
    assert "network_matrix" in telemetry
    assert telemetry["network_matrix"]["lan_ip"] == "10.0.0.1"
    assert telemetry["network_matrix"]["lan_connect_addr"] == "10.0.0.1:8211"
    assert "source_of_truth_battlemetrics" in telemetry
    assert "backup_log_scraper" in telemetry


def test_tracker_probe_local_logs_mock():
    tracker = CommunityTracker("TestServer", "test.duckdns.org")

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "Aug 30 06:00:00 server: Created public lobby session [EOS-12345]\n"

    with patch("subprocess.run", return_value=fake_proc), patch("os.name", "posix"):
        res = tracker.probe_local_logs()
        assert res["registered"] is True
        assert tracker.log_registered is True
        assert tracker.log_first_seen is not None
