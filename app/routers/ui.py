"""User Interface router module.

Provides endpoints for serving HTML views and UI templates.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logger import log

# pylint: disable=unused-import
from app.routers.deps import get_current_user_optional, settings  # noqa: F401

# pylint: enable=unused-import


router = APIRouter(tags=["User Interface"])


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
    template_path = Path(__file__).resolve().parent.parent / "templates" / "index.html"  # noqa: ASYNC240
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading template file at %s: %s", template_path, err)
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
    template_path = Path(__file__).resolve().parent.parent / "templates" / "login.html"  # noqa: ASYNC240
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading login template at %s: %s", template_path, err)
    # Fallback minimal page — template creation is covered in Task 3.5
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
    template_path = Path(__file__).resolve().parent.parent / "templates" / "metrics.html"  # noqa: ASYNC240
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading template file at %s: %s", template_path, err)
    return HTMLResponse("<h2>Palworld Observability Dashboard</h2><p>Template loading...</p>")


@router.get("/feedback", response_class=HTMLResponse)
async def serve_feedback_page() -> HTMLResponse:
    """Serves the standalone feedback submission and issue tracker page.

    Returns:
        HTMLResponse: Rendered feedback page HTML content.
    """
    template_path = Path(__file__).resolve().parent.parent / "templates" / "feedback.html"  # noqa: ASYNC240
    if template_path.exists():
        try:
            return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("Error reading template file at %s: %s", template_path, err)
    return HTMLResponse("<h2>Feedback & Issue Tracker</h2><p>Template loading...</p>")
