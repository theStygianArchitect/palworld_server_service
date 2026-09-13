"""Telemetry & Observability routing.

Provides endpoints for real-time WebSocket telemetry, historical metrics,
log tailing, and network diagnostics.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from app.api.schemas import (
    MetricBucketResponse,
    MetricFlushResponse,
    MetricHistoryResponse,
    MetricPruneResponse,
    MetricSummaryResponse,
)
from app.core.logger import log
from app.database import UserRecord
from app.routers.deps import (
    engine,
    flush_metrics_buffer,
    get_tracker_telemetry_kwargs,
    metrics_buffer,
    metrics_buffer_lock,
    metrics_db,
    perm_admin,
    perm_logs_view,
    perm_view,
    settings,
)

router = APIRouter(tags=["Telemetry & Observability"])


@router.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Websocket connection endpoint for real-time telemetry streaming.

    Args:
        websocket (WebSocket): Inbound client WebSocket connection.
    """
    await engine.register_socket(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect as err:
        log.debug("WebSocket client disconnected: %s", err)
        engine.unregister_socket(websocket)


@router.get("/api/tracker/community")
async def get_community_tracker_data() -> dict[str, Any]:
    """Returns combined 3-section discovery hub, Steam A2S ping, and security matrix.

    Returns:
        dict[str, Any]: Combined discovery, A2S telemetry, and security matrix payload.
    """
    data = await engine.tracker.get_combined_telemetry(**get_tracker_telemetry_kwargs())
    return {"status": "success", "data": data}


@router.get("/api/logs")
async def get_logs(
    tail: int = 200,
    filter_query: str | None = Query(default=None, alias="filter"),
    level: str = "ALL",
    _: UserRecord = Depends(perm_logs_view),
) -> dict[str, Any]:
    """Retrieves sanitized recent Palworld engine log lines.

    Args:
        tail (int): Number of recent lines to retrieve.
        filter_query (str | None): Keyword or regex filter.
        level (str): Category filter (ALL, ENGINE, EOS, WARN_ERROR).
        _: Enforces logs:view permission.

    Returns:
        dict[str, Any]: Log lines array and retrieval metadata.
    """
    return engine.tracker.read_server_logs(tail=tail, filter_query=filter_query, level=level)


@router.get("/api/logs/download")
async def download_logs(
    _: UserRecord = Depends(perm_logs_view),
) -> PlainTextResponse:
    """Streams full sanitized Palworld engine log file as an attachment.

    Args:
        _: Enforces logs:view permission.

    Returns:
        PlainTextResponse: Raw text stream with attachment headers.
    """
    result = engine.tracker.read_server_logs(tail=2000, level="ALL")
    content = "\n".join(result.get("lines", []))
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"palworld_engine_{timestamp}.log"
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/diagnostics/network-test")
async def run_network_diagnostics_test(
    _: UserRecord = Depends(perm_admin),
) -> dict[str, Any]:
    """Executes active network, NAT, and rubberbanding diagnostics.

    Evaluates gateway ping/jitter, internet ping/jitter, engine frame rate,
    and kernel UDP drops to isolate causes of player rubberbanding.

    Args:
        _: Enforces admin permission.

    Returns:
        dict[str, Any]: Diagnostic results, verdict, and recommendations.
    """
    engine_metrics = await engine.get_engine_metrics()
    fps = float(engine_metrics.get("server_fps", 60.0))
    frame_time = float(engine_metrics.get("server_frame_time_ms", 16.6))

    diag_result = await engine.tracker.run_network_diagnostics(
        server_fps=fps,
        server_frame_time_ms=frame_time,
    )
    return {
        "status": "success",
        "data": diag_result,
    }


@router.get(
    "/api/metrics/history",
    response_model=MetricHistoryResponse,
    dependencies=[Depends(perm_view)],
)
async def get_metrics_history(
    window: Literal["1h", "24h", "7d", "30d"] = Query(
        default="24h",
        description="Historical aggregation time window ('1h', '24h', '7d', '30d')",
    ),
) -> MetricHistoryResponse:
    """Retrieves downsampled time-series aggregation buckets for the requested window.

    Args:
        window: Selected time window filter.

    Returns:
        MetricHistoryResponse containing time-series buckets.
    """
    await flush_metrics_buffer()
    buckets_data = await asyncio.to_thread(metrics_db.get_history, window)
    response_buckets = [
        MetricBucketResponse(
            bucket_timestamp=b.bucket_timestamp,
            avg_fps=b.avg_fps,
            min_fps=b.min_fps,
            max_fps=b.max_fps,
            avg_frame_time_ms=b.avg_frame_time_ms,
            avg_players=b.avg_players,
            max_players=b.max_players,
            avg_cpu_pct=b.avg_cpu_pct,
            max_cpu_pct=b.max_cpu_pct,
            avg_ram_pct=b.avg_ram_pct,
            max_ram_pct=b.max_ram_pct,
            sample_count=b.sample_count,
        )
        for b in buckets_data
    ]
    return MetricHistoryResponse(
        window=window,
        total_buckets=len(response_buckets),
        buckets=response_buckets,
    )


@router.get(
    "/api/metrics/summary",
    response_model=MetricSummaryResponse,
    dependencies=[Depends(perm_view)],
)
async def get_metrics_summary() -> MetricSummaryResponse:
    """Retrieves statistical KPI summary across performance and telemetry over a rolling 30-day window.

    Returns:
        MetricSummaryResponse with 30-day aggregates.
    """
    await flush_metrics_buffer()
    summary = await asyncio.to_thread(metrics_db.get_30_day_summary)
    async with metrics_buffer_lock:
        buffered_count = len(metrics_buffer)
    return MetricSummaryResponse(
        total_samples=summary.total_samples,
        peak_players=summary.peak_players,
        avg_players=summary.avg_players,
        lowest_fps=summary.lowest_fps,
        avg_fps=summary.avg_fps,
        peak_cpu_pct=summary.peak_cpu_pct,
        avg_cpu_pct=summary.avg_cpu_pct,
        peak_ram_pct=summary.peak_ram_pct,
        avg_ram_pct=summary.avg_ram_pct,
        buffered_samples=buffered_count,
        window_start=summary.window_start,
        window_end=summary.window_end,
    )


@router.post(
    "/api/metrics/flush",
    response_model=MetricFlushResponse,
    dependencies=[Depends(perm_admin)],
)
async def flush_metrics() -> MetricFlushResponse:
    """Manually flushes in-memory buffered metrics to disk.

    Returns:
        MetricFlushResponse with flushed and remaining snapshot counts.
    """
    flushed = await flush_metrics_buffer()
    async with metrics_buffer_lock:
        remaining = len(metrics_buffer)
    return MetricFlushResponse(
        status="success",
        flushed_snapshots=flushed,
        buffered_remaining=remaining,
    )


@router.post(
    "/api/metrics/prune",
    response_model=MetricPruneResponse,
    dependencies=[Depends(perm_admin)],
)
async def prune_metrics(
    days: int | None = Query(
        default=None,
        ge=1,
        le=365,
        description="Optional retention window override in days",
    ),
) -> MetricPruneResponse:
    """Manually triggers pruning of metrics older than the retention threshold.

    Args:
        days: Optional retention window override in days (default: configured settings).

    Returns:
        MetricPruneResponse with total pruned records count.
    """
    retention = days if days is not None else settings.metrics_retention_days
    pruned = await asyncio.to_thread(metrics_db.prune_older_than, retention)
    return MetricPruneResponse(
        status="success",
        pruned_records=pruned,
        retention_days=retention,
    )
