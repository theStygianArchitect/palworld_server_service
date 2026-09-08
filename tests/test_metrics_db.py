"""Unit tests for dedicated SQLite metrics database and rolling 30-day retention store.

Verifies schema creation, time-series snapshot recording, rolling 30-day retention
pruning, downsampled historical bucketing, and 30-day statistical KPI summaries.
"""

from __future__ import annotations

import datetime
from collections.abc import Generator
from pathlib import Path

import pytest

from app.database.metric_models import (
    Metric30DaySummary,
    MetricBucketRecord,
    MetricSnapshotRecord,
)
from app.database.metrics_db import MetricsDatabaseManager


@pytest.fixture(name="metrics_db")
def metrics_db_fixture(tmp_path: Path) -> Generator[MetricsDatabaseManager, None, None]:
    """Provides an isolated MetricsDatabaseManager instance backed by a temporary SQLite file."""
    db_file = tmp_path / "test_palmetrics.db"
    manager = MetricsDatabaseManager(str(db_file))
    manager.initialize()
    yield manager
    manager.close()


def test_metrics_schema_initialization(metrics_db: MetricsDatabaseManager) -> None:
    """Verifies that the metrics schema and indexes are created idempotently."""
    # Calling initialize again should succeed without error
    metrics_db.initialize()
    snapshots = metrics_db.get_recent_snapshots(limit=10)
    assert snapshots == []


def test_record_snapshot_and_query(metrics_db: MetricsDatabaseManager) -> None:
    """Tests inserting telemetry snapshots and querying them back."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    ts1 = (now_utc - datetime.timedelta(minutes=2)).isoformat()
    ts2 = now_utc.isoformat()

    snap1 = MetricSnapshotRecord(
        id=None,
        timestamp=ts1,
        server_fps=59.8,
        server_frame_time_ms=16.7,
        uptime_seconds=3600,
        active_players=4,
        max_players=32,
        cpu_avg_pct=14.5,
        host_ram_used_gb=8.2,
        host_ram_total_gb=32.0,
        host_ram_pct=25.6,
        cgroup_ram_used_gb=6.1,
        cgroup_ram_pct=43.5,
        disk_used_gb=22.4,
        disk_pct=18.0,
        net_rx_rate_kbps=128.5,
        net_tx_rate_kbps=256.0,
    )
    snap2 = MetricSnapshotRecord(
        id=None,
        timestamp=ts2,
        server_fps=60.0,
        server_frame_time_ms=16.6,
        uptime_seconds=3720,
        active_players=8,
        max_players=32,
        cpu_avg_pct=18.2,
        host_ram_used_gb=8.5,
        host_ram_total_gb=32.0,
        host_ram_pct=26.5,
        cgroup_ram_used_gb=6.4,
        cgroup_ram_pct=45.7,
        disk_used_gb=22.4,
        disk_pct=18.0,
        net_rx_rate_kbps=210.0,
        net_tx_rate_kbps=450.0,
    )

    id1 = metrics_db.record_snapshot(snap1)
    id2 = metrics_db.record_snapshot(snap2)

    assert id1 > 0
    assert id2 > id1

    recent = metrics_db.get_recent_snapshots(limit=10)
    assert len(recent) == 2
    assert recent[0].timestamp == ts1
    assert recent[0].server_fps == 59.8
    assert recent[0].active_players == 4
    assert recent[1].timestamp == ts2
    assert recent[1].server_fps == 60.0
    assert recent[1].active_players == 8


def test_rolling_30_day_retention_pruning(metrics_db: MetricsDatabaseManager) -> None:
    """Verifies that records older than 30 days are purged while newer records are preserved."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # 3 stale records: 32, 45, and 60 days old
    stale_ts = [
        (now_utc - datetime.timedelta(days=32)).isoformat(),
        (now_utc - datetime.timedelta(days=45)).isoformat(),
        (now_utc - datetime.timedelta(days=60)).isoformat(),
    ]
    for ts in stale_ts:
        metrics_db.record_snapshot(
            MetricSnapshotRecord(
                id=None,
                timestamp=ts,
                server_fps=55.0,
                server_frame_time_ms=18.0,
                uptime_seconds=1000,
                active_players=2,
                max_players=32,
                cpu_avg_pct=10.0,
                host_ram_used_gb=7.0,
                host_ram_total_gb=32.0,
                host_ram_pct=21.8,
                cgroup_ram_used_gb=5.0,
                cgroup_ram_pct=35.7,
                disk_used_gb=20.0,
                disk_pct=15.0,
                net_rx_rate_kbps=50.0,
                net_tx_rate_kbps=50.0,
            )
        )

    # 4 active records: 29 days, 7 days, 1 day, and 5 minutes old
    active_ts = [
        (now_utc - datetime.timedelta(days=29)).isoformat(),
        (now_utc - datetime.timedelta(days=7)).isoformat(),
        (now_utc - datetime.timedelta(days=1)).isoformat(),
        (now_utc - datetime.timedelta(minutes=5)).isoformat(),
    ]
    for ts in active_ts:
        metrics_db.record_snapshot(
            MetricSnapshotRecord(
                id=None,
                timestamp=ts,
                server_fps=60.0,
                server_frame_time_ms=16.6,
                uptime_seconds=5000,
                active_players=6,
                max_players=32,
                cpu_avg_pct=15.0,
                host_ram_used_gb=8.0,
                host_ram_total_gb=32.0,
                host_ram_pct=25.0,
                cgroup_ram_used_gb=6.0,
                cgroup_ram_pct=42.8,
                disk_used_gb=21.0,
                disk_pct=16.0,
                net_rx_rate_kbps=100.0,
                net_tx_rate_kbps=100.0,
            )
        )

    # Total before pruning
    assert len(metrics_db.get_recent_snapshots(limit=20)) == 7

    # Prune with 30-day retention
    pruned_count = metrics_db.prune_older_than(days=30)
    assert pruned_count == 3

    # Assert remaining records are exactly the 4 newer ones
    remaining = metrics_db.get_recent_snapshots(limit=20)
    assert len(remaining) == 4
    for r in remaining:
        assert r.timestamp in active_ts


def test_history_bucketing_windows(metrics_db: MetricsDatabaseManager) -> None:
    """Tests aggregation queries across different window intervals."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Populate 6 data points over the last 2 hours
    for i in range(6):
        sample_ts = (now_utc - datetime.timedelta(minutes=i * 20)).isoformat()
        metrics_db.record_snapshot(
            MetricSnapshotRecord(
                id=None,
                timestamp=sample_ts,
                server_fps=50.0 + (i * 2),
                server_frame_time_ms=16.0 + i,
                uptime_seconds=1000 + i,
                active_players=i + 1,
                max_players=32,
                cpu_avg_pct=10.0 + i,
                host_ram_used_gb=8.0,
                host_ram_total_gb=32.0,
                host_ram_pct=25.0 + i,
                cgroup_ram_used_gb=6.0,
                cgroup_ram_pct=40.0,
                disk_used_gb=20.0,
                disk_pct=15.0,
                net_rx_rate_kbps=100.0,
                net_tx_rate_kbps=200.0,
            )
        )

    # Query 1h window
    h1 = metrics_db.get_history(window="1h")
    assert isinstance(h1, list)
    for b in h1:
        assert isinstance(b, MetricBucketRecord)
        assert b.sample_count > 0

    # Query 24h window
    h24 = metrics_db.get_history(window="24h")
    assert isinstance(h24, list)
    assert len(h24) > 0

    # Query 7d window
    h7d = metrics_db.get_history(window="7d")
    assert isinstance(h7d, list)

    # Query 30d window
    h30d = metrics_db.get_history(window="30d")
    assert isinstance(h30d, list)


def test_30_day_summary_calculation(metrics_db: MetricsDatabaseManager) -> None:
    """Tests rolling 30-day statistical KPI overview on empty and populated database."""
    # 1. Empty database summary
    empty_summary = metrics_db.get_30_day_summary()
    assert isinstance(empty_summary, Metric30DaySummary)
    assert empty_summary.total_samples == 0
    assert empty_summary.peak_players == 0
    assert empty_summary.avg_players == 0.0

    # 2. Populated database
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    metrics_db.record_snapshot(
        MetricSnapshotRecord(
            id=None,
            timestamp=(now_utc - datetime.timedelta(days=2)).isoformat(),
            server_fps=45.0,
            server_frame_time_ms=22.2,
            uptime_seconds=5000,
            active_players=3,
            max_players=32,
            cpu_avg_pct=20.0,
            host_ram_used_gb=10.0,
            host_ram_total_gb=32.0,
            host_ram_pct=31.2,
            cgroup_ram_used_gb=7.0,
            cgroup_ram_pct=50.0,
            disk_used_gb=22.0,
            disk_pct=17.0,
            net_rx_rate_kbps=150.0,
            net_tx_rate_kbps=300.0,
        )
    )
    metrics_db.record_snapshot(
        MetricSnapshotRecord(
            id=None,
            timestamp=(now_utc - datetime.timedelta(days=1)).isoformat(),
            server_fps=60.0,
            server_frame_time_ms=16.6,
            uptime_seconds=10000,
            active_players=15,
            max_players=32,
            cpu_avg_pct=40.0,
            host_ram_used_gb=12.0,
            host_ram_total_gb=32.0,
            host_ram_pct=37.5,
            cgroup_ram_used_gb=8.5,
            cgroup_ram_pct=60.7,
            disk_used_gb=22.0,
            disk_pct=17.0,
            net_rx_rate_kbps=400.0,
            net_tx_rate_kbps=800.0,
        )
    )

    summary = metrics_db.get_30_day_summary()
    assert summary.total_samples == 2
    assert summary.peak_players == 15
    assert summary.avg_players == 9.0
    assert summary.lowest_fps == 45.0
    assert summary.avg_fps == 52.5
    assert summary.peak_cpu_pct == 40.0
    assert summary.avg_cpu_pct == 30.0
    assert summary.peak_ram_pct == 37.5
    assert summary.avg_ram_pct == 34.35
    assert summary.window_start != ""
    assert summary.window_end != ""


def test_record_snapshots_batch(metrics_db: MetricsDatabaseManager) -> None:
    """Tests batch inserting multiple metric snapshots within a single transaction."""
    # 1. Empty batch
    count_empty = metrics_db.record_snapshots_batch([])
    assert count_empty == 0

    # 2. Batch of 3 snapshots
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    batch = [
        MetricSnapshotRecord(
            id=None,
            timestamp=(now_utc - datetime.timedelta(seconds=20)).isoformat(),
            server_fps=59.0,
            server_frame_time_ms=16.9,
            uptime_seconds=100,
            active_players=3,
            max_players=32,
            cpu_avg_pct=12.0,
            host_ram_used_gb=8.0,
            host_ram_total_gb=32.0,
            host_ram_pct=25.0,
            cgroup_ram_used_gb=5.5,
            cgroup_ram_pct=39.2,
            disk_used_gb=20.0,
            disk_pct=15.0,
            net_rx_rate_kbps=100.0,
            net_tx_rate_kbps=200.0,
        ),
        MetricSnapshotRecord(
            id=None,
            timestamp=(now_utc - datetime.timedelta(seconds=10)).isoformat(),
            server_fps=59.5,
            server_frame_time_ms=16.8,
            uptime_seconds=110,
            active_players=5,
            max_players=32,
            cpu_avg_pct=14.0,
            host_ram_used_gb=8.1,
            host_ram_total_gb=32.0,
            host_ram_pct=25.3,
            cgroup_ram_used_gb=5.6,
            cgroup_ram_pct=40.0,
            disk_used_gb=20.0,
            disk_pct=15.0,
            net_rx_rate_kbps=120.0,
            net_tx_rate_kbps=220.0,
        ),
        MetricSnapshotRecord(
            id=None,
            timestamp=now_utc.isoformat(),
            server_fps=60.0,
            server_frame_time_ms=16.6,
            uptime_seconds=120,
            active_players=8,
            max_players=32,
            cpu_avg_pct=16.0,
            host_ram_used_gb=8.2,
            host_ram_total_gb=32.0,
            host_ram_pct=25.6,
            cgroup_ram_used_gb=5.7,
            cgroup_ram_pct=40.7,
            disk_used_gb=20.0,
            disk_pct=15.0,
            net_rx_rate_kbps=150.0,
            net_tx_rate_kbps=250.0,
        ),
    ]
    inserted = metrics_db.record_snapshots_batch(batch)
    assert inserted == 3

    recent = metrics_db.get_recent_snapshots(limit=10)
    assert len(recent) == 3
    assert recent[0].active_players == 3
    assert recent[1].active_players == 5
    assert recent[2].active_players == 8
