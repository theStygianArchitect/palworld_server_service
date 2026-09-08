"""SQLite time-series metrics engine and rolling 30-day retention store.

Provides transactional persistence, downsampled aggregation queries, and
automated rolling 30-day retention pruning for server performance, player count,
and hardware telemetry metrics in compliance with 3 AM defensive standards.
"""

from __future__ import annotations

import datetime
import sqlite3
import threading
from pathlib import Path

from app.core.logger import log
from app.database.metric_models import (
    Metric30DaySummary,
    MetricBucketRecord,
    MetricSnapshotRecord,
)

METRICS_SCHEMA_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS metrics_timeseries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    server_fps REAL NOT NULL,
    server_frame_time_ms REAL NOT NULL,
    uptime_seconds INTEGER NOT NULL,
    active_players INTEGER NOT NULL,
    max_players INTEGER NOT NULL,
    cpu_avg_pct REAL NOT NULL,
    host_ram_used_gb REAL NOT NULL,
    host_ram_total_gb REAL NOT NULL,
    host_ram_pct REAL NOT NULL,
    cgroup_ram_used_gb REAL NOT NULL,
    cgroup_ram_pct REAL NOT NULL,
    disk_used_gb REAL NOT NULL,
    disk_pct REAL NOT NULL,
    net_rx_rate_kbps REAL NOT NULL,
    net_tx_rate_kbps REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics_timeseries(timestamp);
"""

# Static parameterized query strings to eliminate any potential Bandit B608 issues
HISTORY_QUERY_1H = """
SELECT
    substr(timestamp, 1, 16) || ':00Z' as bucket,
    ROUND(AVG(server_fps), 2) as avg_fps,
    ROUND(MIN(server_fps), 2) as min_fps,
    ROUND(MAX(server_fps), 2) as max_fps,
    ROUND(AVG(server_frame_time_ms), 2) as avg_frame_time_ms,
    ROUND(AVG(active_players), 2) as avg_players,
    MAX(active_players) as max_players,
    ROUND(AVG(cpu_avg_pct), 2) as avg_cpu_pct,
    ROUND(MAX(cpu_avg_pct), 2) as max_cpu_pct,
    ROUND(AVG(host_ram_pct), 2) as avg_ram_pct,
    ROUND(MAX(host_ram_pct), 2) as max_ram_pct,
    COUNT(id) as sample_count
FROM metrics_timeseries
WHERE timestamp >= ?
GROUP BY bucket
ORDER BY bucket ASC
"""

HISTORY_QUERY_24H = """
SELECT
    substr(timestamp, 1, 15) || '0:00Z' as bucket,
    ROUND(AVG(server_fps), 2) as avg_fps,
    ROUND(MIN(server_fps), 2) as min_fps,
    ROUND(MAX(server_fps), 2) as max_fps,
    ROUND(AVG(server_frame_time_ms), 2) as avg_frame_time_ms,
    ROUND(AVG(active_players), 2) as avg_players,
    MAX(active_players) as max_players,
    ROUND(AVG(cpu_avg_pct), 2) as avg_cpu_pct,
    ROUND(MAX(cpu_avg_pct), 2) as max_cpu_pct,
    ROUND(AVG(host_ram_pct), 2) as avg_ram_pct,
    ROUND(MAX(host_ram_pct), 2) as max_ram_pct,
    COUNT(id) as sample_count
FROM metrics_timeseries
WHERE timestamp >= ?
GROUP BY bucket
ORDER BY bucket ASC
"""

HISTORY_QUERY_7D = """
SELECT
    substr(timestamp, 1, 13) || ':00:00Z' as bucket,
    ROUND(AVG(server_fps), 2) as avg_fps,
    ROUND(MIN(server_fps), 2) as min_fps,
    ROUND(MAX(server_fps), 2) as max_fps,
    ROUND(AVG(server_frame_time_ms), 2) as avg_frame_time_ms,
    ROUND(AVG(active_players), 2) as avg_players,
    MAX(active_players) as max_players,
    ROUND(AVG(cpu_avg_pct), 2) as avg_cpu_pct,
    ROUND(MAX(cpu_avg_pct), 2) as max_cpu_pct,
    ROUND(AVG(host_ram_pct), 2) as avg_ram_pct,
    ROUND(MAX(host_ram_pct), 2) as max_ram_pct,
    COUNT(id) as sample_count
FROM metrics_timeseries
WHERE timestamp >= ?
GROUP BY bucket
ORDER BY bucket ASC
"""

HISTORY_QUERY_30D = """
SELECT
    substr(timestamp, 1, 10) || 'T00:00:00Z' as bucket,
    ROUND(AVG(server_fps), 2) as avg_fps,
    ROUND(MIN(server_fps), 2) as min_fps,
    ROUND(MAX(server_fps), 2) as max_fps,
    ROUND(AVG(server_frame_time_ms), 2) as avg_frame_time_ms,
    ROUND(AVG(active_players), 2) as avg_players,
    MAX(active_players) as max_players,
    ROUND(AVG(cpu_avg_pct), 2) as avg_cpu_pct,
    ROUND(MAX(cpu_avg_pct), 2) as max_cpu_pct,
    ROUND(AVG(host_ram_pct), 2) as avg_ram_pct,
    ROUND(MAX(host_ram_pct), 2) as max_ram_pct,
    COUNT(id) as sample_count
FROM metrics_timeseries
WHERE timestamp >= ?
GROUP BY bucket
ORDER BY bucket ASC
"""

SUMMARY_QUERY_30D = """
SELECT
    COUNT(id) as total_samples,
    COALESCE(MAX(active_players), 0) as peak_players,
    COALESCE(ROUND(AVG(active_players), 2), 0.0) as avg_players,
    COALESCE(ROUND(MIN(server_fps), 2), 0.0) as lowest_fps,
    COALESCE(ROUND(AVG(server_fps), 2), 0.0) as avg_fps,
    COALESCE(ROUND(MAX(cpu_avg_pct), 2), 0.0) as peak_cpu_pct,
    COALESCE(ROUND(AVG(cpu_avg_pct), 2), 0.0) as avg_cpu_pct,
    COALESCE(ROUND(MAX(host_ram_pct), 2), 0.0) as peak_ram_pct,
    COALESCE(ROUND(AVG(host_ram_pct), 2), 0.0) as avg_ram_pct,
    COALESCE(MIN(timestamp), '') as window_start,
    COALESCE(MAX(timestamp), '') as window_end
FROM metrics_timeseries
WHERE timestamp >= ?
"""


class MetricsDatabaseManager:
    """Manages dedicated SQLite storage and downsampling queries for metrics."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        """Initializes the metrics database manager instance.

        Args:
            db_path: Path to the SQLite metrics database file.
        """
        if db_path is None:
            self.db_path = str(Path.home() / ".palmanager" / "metrics.db")
        else:
            self.db_path = str(db_path)

        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def get_connection(self) -> sqlite3.Connection:
        """Acquires a pooled, thread-safe SQLite connection with row factory.

        Returns:
            sqlite3.Connection: Active database connection.
        """
        with self._lock:
            if self._connection is None:
                db_file = Path(self.db_path)
                try:
                    db_file.parent.mkdir(parents=True, exist_ok=True)
                except PermissionError as err:
                    log.warning(
                        "Permission denied creating metrics directory %s (%s). Falling back to home directory.",
                        db_file.parent,
                        err,
                    )
                    fallback_dir = Path.home() / ".palmanager"
                    try:
                        fallback_dir.mkdir(parents=True, exist_ok=True)
                        self.db_path = str(fallback_dir / "metrics.db")
                    except OSError as sub_err:
                        log.error("Failed to create fallback metrics directory %s: %s", fallback_dir, sub_err)
                except OSError as err:
                    log.warning(
                        "OS error creating metrics directory %s (%s). Falling back to home directory.",
                        db_file.parent,
                        err,
                    )
                    fallback_dir = Path.home() / ".palmanager"
                    try:
                        fallback_dir.mkdir(parents=True, exist_ok=True)
                        self.db_path = str(fallback_dir / "metrics.db")
                    except OSError as sub_err:
                        log.error("Failed to create fallback metrics directory %s: %s", fallback_dir, sub_err)

                conn = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,
                    timeout=30.0,
                )
                conn.row_factory = sqlite3.Row
                self._connection = conn
            return self._connection

    def initialize(self) -> None:
        """Executes schema DDL and indexes idempotently."""
        with self._lock:
            conn = self.get_connection()
            try:
                conn.executescript(METRICS_SCHEMA_DDL)
                conn.commit()
                log.info("Initialized metrics time-series SQLite database at %s", self.db_path)
            except sqlite3.OperationalError as err:
                log.error("Operational error initializing metrics schema at %s: %s", self.db_path, err)
                raise
            except sqlite3.DatabaseError as err:
                log.error("Database error initializing metrics schema at %s: %s", self.db_path, err)
                raise

    def record_snapshot(self, snapshot: MetricSnapshotRecord) -> int:
        """Inserts a single metric telemetry snapshot into the time-series store.

        Args:
            snapshot: Structured metric snapshot dataclass.

        Returns:
            int: Inserted record primary key ID.
        """
        insert_sql = """
        INSERT INTO metrics_timeseries (
            timestamp, server_fps, server_frame_time_ms, uptime_seconds,
            active_players, max_players, cpu_avg_pct, host_ram_used_gb,
            host_ram_total_gb, host_ram_pct, cgroup_ram_used_gb, cgroup_ram_pct,
            disk_used_gb, disk_pct, net_rx_rate_kbps, net_tx_rate_kbps
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    insert_sql,
                    (
                        snapshot.timestamp,
                        snapshot.server_fps,
                        snapshot.server_frame_time_ms,
                        snapshot.uptime_seconds,
                        snapshot.active_players,
                        snapshot.max_players,
                        snapshot.cpu_avg_pct,
                        snapshot.host_ram_used_gb,
                        snapshot.host_ram_total_gb,
                        snapshot.host_ram_pct,
                        snapshot.cgroup_ram_used_gb,
                        snapshot.cgroup_ram_pct,
                        snapshot.disk_used_gb,
                        snapshot.disk_pct,
                        snapshot.net_rx_rate_kbps,
                        snapshot.net_tx_rate_kbps,
                    ),
                )
                conn.commit()
                return int(cursor.lastrowid or 0)
            except sqlite3.IntegrityError as err:
                log.error("Integrity error recording metric snapshot: %s", err)
                raise
            except sqlite3.OperationalError as err:
                log.error("Operational error recording metric snapshot: %s", err)
                raise
            except sqlite3.DatabaseError as err:
                log.error("Database error recording metric snapshot: %s", err)
                raise

    def record_snapshots_batch(self, snapshots: list[MetricSnapshotRecord]) -> int:
        """Inserts a batch of telemetry snapshots within a single atomic transaction.

        Args:
            snapshots: List of MetricSnapshotRecord instances to persist.

        Returns:
            int: Number of snapshots successfully inserted.
        """
        if not snapshots:
            return 0

        insert_sql = """
        INSERT INTO metrics_timeseries (
            timestamp, server_fps, server_frame_time_ms, uptime_seconds,
            active_players, max_players, cpu_avg_pct, host_ram_used_gb,
            host_ram_total_gb, host_ram_pct, cgroup_ram_used_gb, cgroup_ram_pct,
            disk_used_gb, disk_pct, net_rx_rate_kbps, net_tx_rate_kbps
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        records_data = [
            (
                s.timestamp,
                s.server_fps,
                s.server_frame_time_ms,
                s.uptime_seconds,
                s.active_players,
                s.max_players,
                s.cpu_avg_pct,
                s.host_ram_used_gb,
                s.host_ram_total_gb,
                s.host_ram_pct,
                s.cgroup_ram_used_gb,
                s.cgroup_ram_pct,
                s.disk_used_gb,
                s.disk_pct,
                s.net_rx_rate_kbps,
                s.net_tx_rate_kbps,
            )
            for s in snapshots
        ]

        with self._lock:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.executemany(insert_sql, records_data)
                conn.commit()
                inserted_count = len(records_data)
                log.info("Batch flushed %d telemetry snapshots to metrics database", inserted_count)
                return inserted_count
            except sqlite3.IntegrityError as err:
                log.error("Integrity error during batch metrics insertion: %s", err)
                conn.rollback()
                raise
            except sqlite3.OperationalError as err:
                log.error("Operational error during batch metrics insertion: %s", err)
                conn.rollback()
                raise
            except sqlite3.DatabaseError as err:
                log.error("Database error during batch metrics insertion: %s", err)
                conn.rollback()
                raise

    def prune_older_than(self, days: int = 30) -> int:
        """Prunes metrics records older than the specified retention duration.

        Args:
            days: Retention window in days (default: 30).

        Returns:
            int: Total number of pruned records deleted from the database.
        """
        cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        cutoff_str = cutoff_dt.isoformat()
        prune_sql = "DELETE FROM metrics_timeseries WHERE timestamp < ?"

        with self._lock:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(prune_sql, (cutoff_str,))
                deleted_count = cursor.rowcount
                conn.commit()
                if deleted_count > 0:
                    log.info(
                        "Pruned %d metric records older than %d days (cutoff: %s)",
                        deleted_count,
                        days,
                        cutoff_str,
                    )
                return max(deleted_count, 0)
            except sqlite3.OperationalError as err:
                log.error("Operational error during metrics pruning: %s", err)
                return 0
            except sqlite3.DatabaseError as err:
                log.error("Database error during metrics pruning: %s", err)
                return 0

    def get_recent_snapshots(self, limit: int = 60) -> list[MetricSnapshotRecord]:
        """Retrieves the most recent raw metric snapshots in chronological order.

        Args:
            limit: Maximum count of recent records to return.

        Returns:
            list[MetricSnapshotRecord]: List of recent snapshots.
        """
        query = """
        SELECT
            id, timestamp, server_fps, server_frame_time_ms, uptime_seconds,
            active_players, max_players, cpu_avg_pct, host_ram_used_gb,
            host_ram_total_gb, host_ram_pct, cgroup_ram_used_gb, cgroup_ram_pct,
            disk_used_gb, disk_pct, net_rx_rate_kbps, net_tx_rate_kbps
        FROM metrics_timeseries
        ORDER BY id DESC
        LIMIT ?
        """
        with self._lock:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(query, (limit,))
                rows = cursor.fetchall()
                records = [
                    MetricSnapshotRecord(
                        id=row["id"],
                        timestamp=row["timestamp"],
                        server_fps=float(row["server_fps"]),
                        server_frame_time_ms=float(row["server_frame_time_ms"]),
                        uptime_seconds=int(row["uptime_seconds"]),
                        active_players=int(row["active_players"]),
                        max_players=int(row["max_players"]),
                        cpu_avg_pct=float(row["cpu_avg_pct"]),
                        host_ram_used_gb=float(row["host_ram_used_gb"]),
                        host_ram_total_gb=float(row["host_ram_total_gb"]),
                        host_ram_pct=float(row["host_ram_pct"]),
                        cgroup_ram_used_gb=float(row["cgroup_ram_used_gb"]),
                        cgroup_ram_pct=float(row["cgroup_ram_pct"]),
                        disk_used_gb=float(row["disk_used_gb"]),
                        disk_pct=float(row["disk_pct"]),
                        net_rx_rate_kbps=float(row["net_rx_rate_kbps"]),
                        net_tx_rate_kbps=float(row["net_tx_rate_kbps"]),
                    )
                    for row in rows
                ]
                records.reverse()
                return records
            except sqlite3.OperationalError as err:
                log.error("Operational error retrieving recent metrics snapshots: %s", err)
                return []
            except sqlite3.DatabaseError as err:
                log.error("Database error retrieving recent metrics snapshots: %s", err)
                return []

    def get_history(self, window: str = "24h") -> list[MetricBucketRecord]:
        """Retrieves downsampled aggregated time-series buckets for the given window.

        Args:
            window: Time range window ('1h', '24h', '7d', or '30d').

        Returns:
            list[MetricBucketRecord]: Ordered list of time-series buckets.
        """
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if window == "1h":
            cutoff_dt = now_utc - datetime.timedelta(hours=1)
            query_sql = HISTORY_QUERY_1H
        elif window == "7d":
            cutoff_dt = now_utc - datetime.timedelta(days=7)
            query_sql = HISTORY_QUERY_7D
        elif window == "30d":
            cutoff_dt = now_utc - datetime.timedelta(days=30)
            query_sql = HISTORY_QUERY_30D
        else:  # Default to 24h
            cutoff_dt = now_utc - datetime.timedelta(hours=24)
            query_sql = HISTORY_QUERY_24H

        cutoff_str = cutoff_dt.isoformat()

        with self._lock:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(query_sql, (cutoff_str,))
                rows = cursor.fetchall()
                return [
                    MetricBucketRecord(
                        bucket_timestamp=str(row["bucket"]),
                        avg_fps=float(row["avg_fps"] or 0.0),
                        min_fps=float(row["min_fps"] or 0.0),
                        max_fps=float(row["max_fps"] or 0.0),
                        avg_frame_time_ms=float(row["avg_frame_time_ms"] or 0.0),
                        avg_players=float(row["avg_players"] or 0.0),
                        max_players=int(row["max_players"] or 0),
                        avg_cpu_pct=float(row["avg_cpu_pct"] or 0.0),
                        max_cpu_pct=float(row["max_cpu_pct"] or 0.0),
                        avg_ram_pct=float(row["avg_ram_pct"] or 0.0),
                        max_ram_pct=float(row["max_ram_pct"] or 0.0),
                        sample_count=int(row["sample_count"] or 0),
                    )
                    for row in rows
                ]
            except sqlite3.OperationalError as err:
                log.error("Operational error retrieving metrics history (%s): %s", window, err)
                return []
            except sqlite3.DatabaseError as err:
                log.error("Database error retrieving metrics history (%s): %s", window, err)
                return []

    def get_30_day_summary(self) -> Metric30DaySummary:
        """Calculates statistical summary KPIs over the rolling 30-day window.

        Returns:
            Metric30DaySummary: High-level KPI aggregations for the rolling 30 days.
        """
        cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        cutoff_str = cutoff_dt.isoformat()

        with self._lock:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(SUMMARY_QUERY_30D, (cutoff_str,))
                row = cursor.fetchone()
                if row is None:
                    return Metric30DaySummary(
                        total_samples=0,
                        peak_players=0,
                        avg_players=0.0,
                        lowest_fps=0.0,
                        avg_fps=0.0,
                        peak_cpu_pct=0.0,
                        avg_cpu_pct=0.0,
                        peak_ram_pct=0.0,
                        avg_ram_pct=0.0,
                        window_start="",
                        window_end="",
                    )
                return Metric30DaySummary(
                    total_samples=int(row["total_samples"] or 0),
                    peak_players=int(row["peak_players"] or 0),
                    avg_players=float(row["avg_players"] or 0.0),
                    lowest_fps=float(row["lowest_fps"] or 0.0),
                    avg_fps=float(row["avg_fps"] or 0.0),
                    peak_cpu_pct=float(row["peak_cpu_pct"] or 0.0),
                    avg_cpu_pct=float(row["avg_cpu_pct"] or 0.0),
                    peak_ram_pct=float(row["peak_ram_pct"] or 0.0),
                    avg_ram_pct=float(row["avg_ram_pct"] or 0.0),
                    window_start=str(row["window_start"] or ""),
                    window_end=str(row["window_end"] or ""),
                )
            except sqlite3.OperationalError as err:
                log.error("Operational error generating 30-day metrics summary: %s", err)
                return Metric30DaySummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "", "")
            except sqlite3.DatabaseError as err:
                log.error("Database error generating 30-day metrics summary: %s", err)
                return Metric30DaySummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "", "")

    def close(self) -> None:
        """Closes the underlying database connection if open."""
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except sqlite3.DatabaseError as err:
                    log.warning("Database error closing metrics connection: %s", err)
                self._connection = None
