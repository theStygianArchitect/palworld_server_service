"""Player moderation routes for Palworld."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import PlayerBanRequest, PlayerKickRequest, PlayerWarnRequest
from app.database.models import UserRecord
from app.routers.deps import engine, perm_player_ban, perm_player_broadcast, perm_player_kick

log = logging.getLogger(__name__)

router = APIRouter(tags=["Player Moderation"])


@router.post("/api/players/kick")
async def handle_kick(
    req: PlayerKickRequest,
    _: UserRecord = Depends(perm_player_kick),
) -> dict[str, Any]:
    """Admin endpoint to kick an online player.

    Args:
        req (PlayerKickRequest): Target player ID and moderation reason.
        _: Enforces player:kick permission.

    Returns:
        dict[str, Any]: Success response.

    Raises:
        HTTPException: If the in-engine kick command fails.
    """
    log.info("Admin request: Kick player %s", req.player_id)
    ok = await engine.kick_player(req.player_id, req.message or "Kicked by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to kick player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} disconnected."}


@router.post("/api/players/ban")
async def handle_ban(
    req: PlayerBanRequest,
    _: UserRecord = Depends(perm_player_ban),
) -> dict[str, Any]:
    """Admin endpoint to ban a player.

    Args:
        req (PlayerBanRequest): Target player ID and moderation reason.
        _: Enforces player:ban permission.

    Returns:
        dict[str, Any]: Success response.

    Raises:
        HTTPException: If the in-engine ban command fails.
    """
    log.info("Admin request: Ban player %s", req.player_id)
    ok = await engine.ban_player(req.player_id, req.message or "Banned by administrator")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to ban player via REST API.")
    return {"status": "success", "message": f"Player {req.player_id} banned."}


@router.post("/api/players/warn")
async def handle_warn(
    req: PlayerWarnRequest,
    _: UserRecord = Depends(perm_player_broadcast),
) -> dict[str, Any]:
    """Admin endpoint to send an announcement across in-game HUD and Discord room.

    Args:
        req (PlayerWarnRequest): Broadcast announcement message string.
        _: Enforces player:broadcast permission.

    Returns:
        dict[str, Any]: Success response.

    Raises:
        HTTPException: If broadcasting the announcement fails.
    """
    log.info("Admin broadcast notice: %s", req.message)
    ok = await engine.send_broadcast(f"[ADMIN NOTICE] {req.message}", mirror_discord=True)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to broadcast message.")
    return {"status": "success", "message": "Broadcast alert sent across in-game HUD and echoed to Discord."}
