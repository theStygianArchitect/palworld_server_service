"""Regression and contract tests for Issue #21.

Verifies route inventory and contract preservation across modular APIRouters decomposition.
"""

from __future__ import annotations

# pylint: disable=missing-function-docstring,redefined-outer-name
# Rationale: Standard pytest testing idioms with fixtures and descriptive test names.
from fastapi.routing import APIRoute, APIWebSocketRoute

from app.main import app

BASELINE_ROUTES = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/"),
    ("GET", "/login"),
    ("GET", "/setup"),
    ("GET", "/api/auth/bootstrap-credentials"),
    ("POST", "/api/auth/ack-bootstrap"),
    ("GET", "/observability"),
    ("GET", "/metrics"),
    ("GET", "/feedback"),
    ("GET", "/feedback/{category_shortcut}"),
    ("WEBSOCKET", "/ws/telemetry"),
    ("GET", "/api/settings"),
    ("POST", "/api/settings"),
    ("GET", "/api/tracker/community"),
    ("POST", "/api/service/reboot"),
    ("POST", "/api/service/reboot/cancel"),
    ("POST", "/api/reboot/cancel"),
    ("POST", "/api/players/kick"),
    ("POST", "/api/players/ban"),
    ("POST", "/api/players/warn"),
    ("GET", "/api/logs"),
    ("GET", "/api/logs/download"),
    ("POST", "/api/diagnostics/network-test"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/auth/audit"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PATCH", "/api/users/{user_id}"),
    ("PATCH", "/api/users/{user_id}/role"),
    ("DELETE", "/api/users/{user_id}"),
    ("POST", "/api/feedback"),
    ("GET", "/api/feedback"),
    ("GET", "/api/metrics/history"),
    ("GET", "/api/metrics/summary"),
    ("POST", "/api/metrics/flush"),
    ("POST", "/api/metrics/prune"),
    ("GET", "/api/system/update/status"),
    ("POST", "/api/system/update/check"),
    ("POST", "/api/system/update/apply"),
    ("GET", "/api/system/deploy/progress"),
    ("POST", "/api/system/update/acknowledge"),
    ("GET", "/api/system/changelog"),
    ("GET", "/canonical"),
    ("GET", "/api/system/version"),
    ("POST", "/api/server/shutdown"),
    ("POST", "/api/server/save"),
    ("GET", "/api/system/tls/status"),
    ("POST", "/api/system/tls/renew"),
}


def _get_app_routes(routes=None) -> set[tuple[str, str]]:
    if routes is None:
        routes = app.routes
    app_routes: set[tuple[str, str]] = set()
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                app_routes.add((method, route.path))
        elif isinstance(route, APIWebSocketRoute):
            app_routes.add(("WEBSOCKET", route.path))
        elif hasattr(route, "original_router"):
            app_routes.update(_get_app_routes(route.original_router.routes))
    return app_routes


def test_route_catalog_total_count() -> None:
    app_routes = _get_app_routes()
    assert len(app_routes) >= 52


def test_baseline_endpoints_exist_and_match_methods() -> None:
    app_routes = _get_app_routes()
    missing = []
    for method, path in BASELINE_ROUTES:
        if (method, path) not in app_routes:
            missing.append(f"{method} {path}")

    assert not missing, f"Missing expected baseline routes: {missing}"


def test_critical_routes_security_dependency_presence() -> None:
    protected_paths = [
        ("POST", "/api/settings"),
        ("POST", "/api/service/reboot"),
        ("GET", "/api/users"),
        ("POST", "/api/users"),
        ("PATCH", "/api/users/{user_id}"),
        ("DELETE", "/api/users/{user_id}"),
        ("POST", "/api/system/tls/renew"),
    ]

    def _find_route(routes, target_method, target_path):
        for route in routes:
            if isinstance(route, APIRoute) and route.path == target_path and target_method in route.methods:
                return route
            if hasattr(route, "original_router"):
                res = _find_route(route.original_router.routes, target_method, target_path)
                if res:
                    return res
        return None

    for method, path in protected_paths:
        found_route = _find_route(app.routes, method, path)

        assert found_route is not None, f"Route not found for dependency check: {method} {path}"

        has_route_deps = bool(found_route.dependant.dependencies)
        assert has_route_deps, f"Protected route {method} {path} has no dependencies!"
