"""User Interface router module.

Provides endpoints for serving HTML views and UI templates.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logger import log
from app.routers.deps import get_current_user_optional

router = APIRouter(tags=["User Interface"])
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _read_template_file(filename: str) -> str | None:
    """Synchronously reads a template file from the templates directory.

    Args:
        filename: File basename within app/templates/.

    Returns:
        str | None: Decoded HTML template content, or None on failure or missing file.
    """
    path = TEMPLATES_DIR / filename
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError as err:
            log.warning("Error reading template file at %s: %s", path, err)
    return None


@router.get("/", response_class=HTMLResponse, response_model=None)
async def serve_dashboard(request: Request) -> HTMLResponse | RedirectResponse:
    """Serves the reactive Tailwind Web management dashboard.

    Authenticated users (valid session cookie or Bearer token) are served the dashboard.
    Unauthenticated requests are redirected to /login, preserving the request URI via ?next=.
    Users present in a first-spin bootstrap state see the setup credential modal automatically
    when they land on /login.

    Args:
        request (Request): Inbound FastAPI HTTP request.

    Returns:
        HTMLResponse | RedirectResponse: Dashboard HTML or redirect to /login with next= param.
    """
    user = get_current_user_optional(request)
    if user is None:
        # Preserve deep-link so the login page can redirect back after successful sign-in.
        return RedirectResponse(url="/login?next=/", status_code=302)
    content = await asyncio.to_thread(_read_template_file, "index.html")
    if content:
        return HTMLResponse(content=content)
    return HTMLResponse("<h2>Palworld Operations Suite Dashboard</h2><p>Template loading...</p>")


@router.get("/login", response_class=HTMLResponse, response_model=None)
async def serve_login_page(request: Request) -> HTMLResponse | RedirectResponse:
    """Serves the login / first-spin setup credential page.

    If the user is already authenticated, redirects to the ?next= param or /.
    Presents the bootstrap credential card automatically when bootstrap is still pending.

    Args:
        request (Request): Inbound FastAPI HTTP request.

    Returns:
        HTMLResponse | RedirectResponse: Login/setup HTML or redirect to dashboard.
    """
    user = get_current_user_optional(request)
    if user is not None:
        next_url = request.query_params.get("next", "/")
        return RedirectResponse(url=next_url, status_code=302)
    content = await asyncio.to_thread(_read_template_file, "login.html")
    if content:
        return HTMLResponse(content=content)
    # Fallback minimal page
    return HTMLResponse(
        "<h2>Palworld Manager — Login</h2><p>Login template not found. Please redeploy.</p>",
        status_code=200,
    )


@router.get("/setup", response_class=HTMLResponse, response_model=None)
async def serve_setup_page(_request: Request) -> HTMLResponse | RedirectResponse:
    """Redirects to /login which hosts the setup credential presentation tab.

    Args:
        _request (Request): Inbound FastAPI HTTP request.

    Returns:
        RedirectResponse: Redirect to /login for unified entry point.
    """
    return RedirectResponse(url="/login", status_code=302)


@router.get("/observability", response_class=HTMLResponse)
@router.get("/metrics", response_class=HTMLResponse)
async def serve_observability_dashboard() -> HTMLResponse:
    """Serves the standalone Prometheus/Grafana style telemetry and observability dashboard.

    Returns:
        HTMLResponse: Rendered observability dashboard HTML content.
    """
    content = await asyncio.to_thread(_read_template_file, "metrics.html")
    if content:
        return HTMLResponse(content=content)
    return HTMLResponse("<h2>Palworld Observability Dashboard</h2><p>Template loading...</p>")


@router.get("/feedback", response_class=HTMLResponse)
async def serve_feedback_page() -> HTMLResponse:
    """Serves the standalone feedback submission and issue tracker page.

    Returns:
        HTMLResponse: Rendered feedback page HTML content.
    """
    content = await asyncio.to_thread(_read_template_file, "feedback.html")
    if content:
        return HTMLResponse(content=content)
    return HTMLResponse("<h2>Feedback & Issue Tracker</h2><p>Template loading...</p>")
