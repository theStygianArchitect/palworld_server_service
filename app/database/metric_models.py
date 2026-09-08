"""Data models and type definitions for time-series metrics tracking.

Defines typed dataclasses for raw telemetry snapshots, historical aggregation
buckets, and 30-day statistical KPI summaries in strict compliance with 3 AM
type isolation standards.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
# Rationale: Relational metrics snapshots capture distinct hardware and engine
# telemetry columns in a single immutable dataclass.
class MetricSnapshotRecord:
    """Represents a discrete time-series snapshot of server and host metrics.

    Attributes:
        id: Primary key unique identifier, or None for unpersisted instances.
        timestamp: ISO-8601 UTC timestamp string of the metric sample.
        server_fps: Dedicated server tick rate (FPS).
        server_frame_time_ms: Average frame time in milliseconds.
        uptime_seconds: Server engine uptime duration in seconds.
        active_players: Current connected player count.
        max_players: Maximum permitted player slots.
        cpu_avg_pct: Average host CPU utilization percentage.
        host_ram_used_gb: Host physical memory used in gigabytes.
        host_ram_total_gb: Total host physical memory in gigabytes.
        host_ram_pct: Host physical memory utilization percentage.
        cgroup_ram_used_gb: Cgroup memory allocation used in gigabytes.
        cgroup_ram_pct: Cgroup memory utilization percentage of allocation limit.
        disk_used_gb: Server data partition disk space used in gigabytes.
        disk_pct: Server data partition disk utilization percentage.
        net_rx_rate_kbps: Inbound network throughput in kilobits per second.
        net_tx_rate_kbps: Outbound network throughput in kilobits per second.
    """

    id: int | None
    timestamp: str
    server_fps: float
    server_frame_time_ms: float
    uptime_seconds: int
    active_players: int
    max_players: int
    cpu_avg_pct: float
    host_ram_used_gb: float
    host_ram_total_gb: float
    host_ram_pct: float
    cgroup_ram_used_gb: float
    cgroup_ram_pct: float
    disk_used_gb: float
    disk_pct: float
    net_rx_rate_kbps: float
    net_tx_rate_kbps: float


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
# Rationale: Downsampled historical aggregation buckets contain aggregate statistics across metrics over a time slice.
class MetricBucketRecord:
    """Represents a time-bucketed aggregation for historical chart queries.

    Attributes:
        bucket_timestamp: ISO-8601 UTC timestamp demarcating the start of the bucket.
        avg_fps: Mean server tick rate across samples in the bucket.
        min_fps: Lowest observed server tick rate in the bucket.
        max_fps: Highest observed server tick rate in the bucket.
        avg_frame_time_ms: Mean server frame time in milliseconds.
        avg_players: Mean active player count across samples.
        max_players: Peak concurrent active player count in the bucket.
        avg_cpu_pct: Mean host CPU utilization percentage.
        max_cpu_pct: Peak host CPU utilization percentage in the bucket.
        avg_ram_pct: Mean host RAM utilization percentage.
        max_ram_pct: Peak host RAM utilization percentage in the bucket.
        sample_count: Number of raw samples aggregated within this time bucket.
    """

    bucket_timestamp: str
    avg_fps: float
    min_fps: float
    max_fps: float
    avg_frame_time_ms: float
    avg_players: float
    max_players: int
    avg_cpu_pct: float
    max_cpu_pct: float
    avg_ram_pct: float
    max_ram_pct: float
    sample_count: int


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
# Rationale: High-level KPI summary requires reporting statistical peaks, minimums,
# and averages over a rolling 30-day window.
class Metric30DaySummary:
    """Statistical summary of server performance and telemetry over a rolling 30-day window.

    Attributes:
        total_samples: Total number of metric data points recorded in the 30-day window.
        peak_players: Maximum concurrent player count recorded during the window.
        avg_players: Mean concurrent player count throughout the window.
        lowest_fps: Minimum server tick rate observed during the window.
        avg_fps: Mean server tick rate observed during the window.
        peak_cpu_pct: Highest host CPU utilization percentage reached.
        avg_cpu_pct: Mean host CPU utilization percentage across the window.
        peak_ram_pct: Highest host RAM utilization percentage reached.
        avg_ram_pct: Mean host RAM utilization percentage across the window.
        window_start: ISO-8601 UTC timestamp of the earliest record in the window.
        window_end: ISO-8601 UTC timestamp of the latest record in the window.
    """

    total_samples: int
    peak_players: int
    avg_players: float
    lowest_fps: float
    avg_fps: float
    peak_cpu_pct: float
    avg_cpu_pct: float
    peak_ram_pct: float
    avg_ram_pct: float
    window_start: str
    window_end: str
