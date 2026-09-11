"""Regression and unit tests for Issue #36: Changelog parsing, API endpoints, and versioning contracts."""
# pylint: disable=missing-function-docstring,redefined-outer-name

import os
import secrets
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.api.schemas import (
    ChangelogResponse,
    PostUpdateSummary,
    UpdateStatusResponse,
)
from app.database.auth import bootstrap_admin_user
from app.engine.changelog import ChangelogParser, get_changelog
from app.main import app, db, metrics_db, settings


@pytest.fixture
def changelog_test_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Isolated FastAPI test client fixture for changelog verification."""
    swaps = [(db, tmp_path / "cl_main.sqlite"), (metrics_db, tmp_path / "cl_metrics.sqlite")]
    prior_paths = []
    prior_updater = settings.updater_enabled
    settings.updater_enabled = False

    for target_db, tmp_file in swaps:
        prior_paths.append((target_db, target_db.db_path))
        target_db.close()
        target_db.db_path = str(tmp_file)
        target_db.initialize()

    bootstrap_admin_user(db=db, default_password=settings.AdminPassword)
    active_client = TestClient(app)
    try:
        yield active_client
    finally:
        active_client.close()
        settings.updater_enabled = prior_updater
        for target_db, original_path in prior_paths:
            target_db.close()
            target_db.db_path = original_path
            target_db.initialize()


def _authenticate_role(client: TestClient, role_name: str) -> str:
    """Creates a user with the specified role and retrieves an authenticated JWT token."""
    login_admin = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": settings.AdminPassword},
    )
    admin_jwt = str(login_admin.json()["token"])
    if role_name == "admin":
        return admin_jwt

    token_pwd = f"UserPass_{secrets.token_hex(8)}!"
    user_payload = {"username": f"user_{role_name}", "password": token_pwd, "role": role_name}
    created = client.post(
        "/api/users",
        json=user_payload,
        headers={"Authorization": f"Bearer {admin_jwt}"},
    )
    assert created.status_code == 200

    auth_res = client.post(
        "/api/auth/login",
        json={"username": user_payload["username"], "password": user_payload["password"]},
    )
    return str(auth_res.json()["token"])


def test_parse_changelog_valid():
    """Asserts that CHANGELOG.md parses correctly, verifying versions 0.2.0-0.1.0, categories, and items."""
    response = get_changelog()
    assert isinstance(response, ChangelogResponse)
    assert response.current_version == "0.2.0"

    # Verify unreleased roadmap items
    assert response.unreleased, "Unreleased categories should not be empty"
    planned_items = [
        item for cat in response.unreleased if cat.category.lower() in {"planned", "unreleased"} for item in cat.items
    ]
    assert any("Modular APIRouters (#21)" in it for it in planned_items)
    assert any("React 19 + Vite SPA" in it for it in planned_items)
    assert any("Atomic INI Persistence (#20)" in it for it in planned_items)

    # Verify historical releases presence
    versions = [r.version for r in response.releases]
    assert "0.2.0" in versions, f"Version 0.2.0 not in parsed releases: {versions}"
    assert "0.1.2" in versions, f"Version 0.1.2 not in parsed releases: {versions}"
    assert "0.1.1" in versions, f"Version 0.1.1 not in parsed releases: {versions}"
    assert "0.1.0" in versions, f"Version 0.1.0 not in parsed releases: {versions}"

    # Verify release 0.2.0
    rel_020 = next(r for r in response.releases if r.version == "0.2.0")
    assert rel_020.date == "2026-09-11"
    categories_020 = {c.category: c.items for c in rel_020.categories}
    assert "Added" in categories_020
    assert "Changed" in categories_020
    assert "Fixed" in categories_020
    assert any("Issue-to-Test Traceability" in it for it in categories_020["Added"])
    assert any("Staged Settings Reboot Persistence" in it for it in categories_020["Added"])
    assert any("Deployer Zero-Drift" in it for it in categories_020["Changed"])
    assert any("Systemd Mount Sandbox Relaxation" in it for it in categories_020["Changed"])
    assert any("Git Dubious Ownership Exit 128" in it for it in categories_020["Fixed"])
    assert any("Frontend HTTP 422 Error Formatting" in it for it in categories_020["Fixed"])

    # Verify release 0.1.2
    rel_012 = next(r for r in response.releases if r.version == "0.1.2")
    assert rel_012.date == "2026-09-10"
    categories_012 = {c.category: c.items for c in rel_012.categories}
    assert "Added" in categories_012
    assert "Fixed" in categories_012
    assert any("Automated HTTPS/TLS via Let's Encrypt" in it for it in categories_012["Added"])
    assert any("TLS Renewal Sudo Privilege" in it for it in categories_012["Fixed"])

    # Verify release 0.1.1
    rel_011 = next(r for r in response.releases if r.version == "0.1.1")
    assert rel_011.date == "2026-09-09"
    categories_011 = {c.category: c.items for c in rel_011.categories}
    assert "Added" in categories_011
    assert "Fixed" in categories_011
    assert any("REST API Hardening" in it for it in categories_011["Added"])
    assert any("Inline JavaScript Syntax Error" in it for it in categories_011["Fixed"])

    # Verify release 0.1.0
    rel_010 = next(r for r in response.releases if r.version == "0.1.0")
    assert rel_010.date == "2026-09-08"
    categories_010 = {c.category: c.items for c in rel_010.categories}
    assert "Added" in categories_010
    assert any("Dynamic Admin Credentials Bootstrap" in it for it in categories_010["Added"])
    assert any("In-App Portal Self-Updater" in it for it in categories_010["Added"])


def test_parse_changelog_missing_file(tmp_path: Path):
    """Verifies graceful degradation when file does not exist (returns empty or fallback without raising)."""
    missing_file = tmp_path / "NONEXISTENT_CHANGELOG.md"
    parser = ChangelogParser(custom_path=missing_file)
    response = parser.parse()

    assert isinstance(response, ChangelogResponse)
    assert response.current_version == "0.2.0"
    assert isinstance(response.releases, list)
    # When file is missing, fallback response provides informational release entry
    assert len(response.releases) >= 1
    assert any(
        "unavailable" in item.lower() for rel in response.releases for cat in rel.categories for item in cat.items
    )


def test_changelog_mtime_caching(tmp_path: Path):
    """Validates that modification of the file triggers cache refresh."""
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text(
        "# Changelog\n\n## [0.2.0] - 2026-09-11\n\n### Added\n- Initial release item\n",
        encoding="utf-8",
    )

    parser = ChangelogParser(custom_path=changelog_file)
    first_response = parser.parse()
    assert first_response.releases[0].categories[0].items == ["Initial release item"]

    # Second parse with unchanged file returns identical cached response object
    cached_response = parser.parse()
    assert cached_response is first_response

    # Update file content and bump file modification timestamp to invalidate cache
    time.sleep(0.05)
    changelog_file.write_text(
        "# Changelog\n\n## [0.2.0] - 2026-09-11\n\n### Added\n- Initial release item\n- Refreshed item\n",
        encoding="utf-8",
    )
    future_mtime = time.time() + 10
    os.utime(changelog_file, (future_mtime, future_mtime))

    refreshed_response = parser.parse()
    assert refreshed_response is not first_response
    items = refreshed_response.releases[0].categories[0].items
    assert "Refreshed item" in items


def test_api_get_changelog(changelog_test_client: TestClient):
    """Uses TestClient to hit GET /api/system/changelog as viewer, operator, and unauthenticated."""
    viewer_token = _authenticate_role(changelog_test_client, "viewer")
    operator_token = _authenticate_role(changelog_test_client, "operator")
    changelog_test_client.cookies.clear()

    non_local = {"X-Forwarded-For": "198.51.100.1"}

    # 1. Unauthenticated non-localhost request must be rejected (401)
    unauth_res = changelog_test_client.get("/api/system/changelog", headers=non_local)
    assert unauth_res.status_code == 401, f"Expected 401 for unauthenticated client, got {unauth_res.status_code}"

    # 2. Authenticated Viewer must succeed (200)
    viewer_headers = {"Authorization": f"Bearer {viewer_token}", **non_local}
    viewer_res = changelog_test_client.get("/api/system/changelog", headers=viewer_headers)
    assert viewer_res.status_code == 200, f"Expected 200 for viewer, got {viewer_res.status_code}"
    viewer_data = viewer_res.json()
    assert viewer_data["current_version"] == "0.2.0"
    assert "releases" in viewer_data
    assert len(viewer_data["releases"]) >= 4

    # 3. Authenticated Operator must succeed (200)
    operator_headers = {"Authorization": f"Bearer {operator_token}", **non_local}
    operator_res = changelog_test_client.get("/api/system/changelog", headers=operator_headers)
    assert operator_res.status_code == 200, f"Expected 200 for operator, got {operator_res.status_code}"
    operator_data = operator_res.json()
    assert operator_data["current_version"] == "0.2.0"
    assert "releases" in operator_data


def test_post_update_summary_deployed_version():
    """Verifies that PostUpdateSummary and UpdateStatusResponse serialize deployed_version."""
    # 1. PostUpdateSummary with deployed_version populated
    summary = PostUpdateSummary(
        status="success",
        deployed_commit="8e0d8f5123456789",
        deployed_commit_short="8e0d8f5",
        deployed_at="2026-09-11T03:20:00Z",
        deployed_version="v0.2.0",
        duration_seconds=15,
        summary="Semantic versioning baseline deployment",
    )
    summary_dict = summary.model_dump()
    assert summary_dict["deployed_version"] == "v0.2.0"
    assert summary_dict["deployed_commit_short"] == "8e0d8f5"
    assert summary_dict["status"] == "success"

    # Round-trip deserialization
    validated = PostUpdateSummary.model_validate(summary_dict)
    assert validated.deployed_version == "v0.2.0"

    # 2. PostUpdateSummary with deployed_version defaulting to None
    summary_none = PostUpdateSummary(
        status="success",
        deployed_commit="8e0d8f5123456789",
        deployed_commit_short="8e0d8f5",
        deployed_at="2026-09-11T03:20:00Z",
    )
    assert summary_none.deployed_version is None
    assert summary_none.model_dump()["deployed_version"] is None

    # 3. UpdateStatusResponse with current_version
    status = UpdateStatusResponse(
        update_available=False,
        current_commit="8e0d8f5",
        latest_commit="8e0d8f5",
        last_checked="2026-09-11T03:20:00Z",
        current_version="0.2.0",
    )
    status_dict = status.model_dump()
    assert status_dict["current_version"] == "0.2.0"
    assert status_dict["update_available"] is False

    # Round-trip deserialization
    status_validated = UpdateStatusResponse.model_validate(status_dict)
    assert status_validated.current_version == "0.2.0"


def test_version_synchronization():
    """Validates that pyproject.toml, app.__version__, and FastAPI version are 0.2.0."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert pyproject_path.is_file()
    content = pyproject_path.read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in content
    assert __version__ == "0.2.0"
    assert app.version == "0.2.0"


def test_api_system_version_endpoint(changelog_test_client: TestClient):
    """Tests GET /api/system/version returns current semantic version."""
    viewer_token = _authenticate_role(changelog_test_client, "viewer")
    changelog_test_client.cookies.clear()

    non_local = {"X-Forwarded-For": "198.51.100.1"}

    # Unauthenticated rejected
    unauth_resp = changelog_test_client.get("/api/system/version", headers=non_local)
    assert unauth_resp.status_code == 401

    # Viewer access
    viewer_resp = changelog_test_client.get(
        "/api/system/version",
        headers={"Authorization": f"Bearer {viewer_token}", **non_local},
    )
    assert viewer_resp.status_code == 200
    assert viewer_resp.json() == {"version": "0.2.0"}


def test_deploy_script_semver_contract():
    """Verifies that deploy.sh extracts DEPLOYED_VERSION and records deployed_version."""
    deploy_sh = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
    assert deploy_sh.is_file()
    content = deploy_sh.read_text(encoding="utf-8")
    assert 'DEPLOYED_VERSION="0.2.0"' in content
    assert '"deployed_version": "v${DEPLOYED_VERSION}"' in content
    assert "CHANGELOG.md" in content
