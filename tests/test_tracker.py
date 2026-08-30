import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.tracker import CommunityTracker


def test_tracker_player_ledger(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = os.path.join(tmpdir, "players.json")
        monkeypatch.setenv("PLAYER_LEDGER_PATH", ledger_path)

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
    telemetry = await tracker.get_combined_telemetry(
        is_multiplay=True,
        host_ip="10.0.0.1",
        public_port=8211,
        server_password="SecretPassword123",  # nosec B106
    )

    assert "top_badge" in telemetry
    assert "discovery_hub" in telemetry
    assert "network_matrix" in telemetry["discovery_hub"]
    assert telemetry["discovery_hub"]["network_matrix"]["lan_ip"] == "10.0.0.1"
    assert telemetry["discovery_hub"]["network_matrix"]["lan_connect_addr"] == "10.0.0.1:8211"
    assert "log_scraper" in telemetry["discovery_hub"]
    assert "pocketpair_master" in telemetry["discovery_hub"]
    assert "a2s_telemetry" in telemetry
    assert "security_matrix" in telemetry
    assert telemetry["security_matrix"]["is_password_protected"] is True


def test_tracker_probe_local_logs_eos_session_extraction():
    tracker = CommunityTracker("TestServer", "test.duckdns.org")

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "Aug 30 06:00:00 server: Created public lobby session [SessionId: 0002a89bf12]\n"

    with patch("subprocess.run", return_value=fake_proc), patch("os.name", "posix"):
        res = tracker.probe_local_logs()
        assert res["registered"] is True
        assert res["session_id"] == "0002a89bf12"
        assert tracker.log_registered is True
        assert tracker.log_session_id == "0002a89bf12"


def test_tracker_parse_a2s_packet():
    # Fake A2S_INFO response packet: \xFF\xFF\xFF\xFF\x49 (I header) + Protocol (0x11) + "Palworld Server\x00" + "Pal/Maps/World\x00" + "Pal\x00" + "Palworld\x00"
    fake_payload = b"\xff\xff\xff\xff\x49\x11Palworld Server\x00Pal/Maps/World\x00Pal\x00Palworld\x00\xe6\x08\x00\x20\x00\x64\x6c\x00\x01\x30\x2e\x32\x2e\x34\x2e\x30\x00"
    res = CommunityTracker.parse_a2s_packet(fake_payload, latency_ms=12.5)

    assert res["responsive"] is True
    assert res["ping_ms"] == 12.5
    assert res["server_name"] == "Palworld Server"
    assert res["map_name"] == "Pal/Maps/World"
    assert res["game"] == "Palworld"
