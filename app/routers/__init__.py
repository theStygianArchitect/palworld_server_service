"""FastAPI API routers package for the Palworld Server Service.

This package organizes the API into modular, domain-specific routers.
"""

from __future__ import annotations

from app.routers import deps
from app.routers.auth import router as auth_router
from app.routers.feedback import router as feedback_router
from app.routers.players import router as players_router
from app.routers.settings import router as settings_router
from app.routers.system import router as system_router
from app.routers.telemetry import router as telemetry_router
from app.routers.ui import router as ui_router

__all__ = [
    "auth_router",
    "deps",
    "feedback_router",
    "players_router",
    "settings_router",
    "system_router",
    "telemetry_router",
    "ui_router",
]
