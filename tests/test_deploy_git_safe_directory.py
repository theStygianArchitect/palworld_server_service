"""Regression test for Issue #32: Git safe.directory ownership handling, exit trap reporting,
and UI step harmonization.
"""
# pylint: disable=redefined-outer-name
# Rationale: Standard pytest idioms with fixtures and self-describing test functions.

from pathlib import Path


def test_deploy_script_git_safe_directory():
    """Verify git safe.directory commands are present before git fetch.

    Regression test for Issue #32: Git safe.directory ownership handling, exit trap reporting,
    and UI step harmonization.
    """
    deploy_sh = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
    assert deploy_sh.is_file(), "deploy.sh must exist"
    content = deploy_sh.read_text(encoding="utf-8")

    # Verify git safe.directory commands are present before git fetch
    assert "safe.directory" in content
    assert 'git config --global --add safe.directory "${REPO_ROOT}"' in content
    assert 'git config --global --add safe.directory "*"' in content


def test_deploy_script_writes_failure_record_on_exit_trap():
    """Verify cleanup_on_exit handles non-zero exit code and writes failure JSON.

    Regression test for Issue #32: Git safe.directory ownership handling, exit trap reporting,
    and UI step harmonization.
    """
    deploy_sh = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
    content = deploy_sh.read_text(encoding="utf-8")

    # Verify cleanup_on_exit handles non-zero exit code and writes failure JSON
    assert "cleanup_on_exit()" in content
    assert 'if [ "${exit_code}" -ne 0 ]' in content
    assert '"status": "failed"' in content
    assert '"summary": "Deployment aborted with error exit code: ${exit_code}"' in content


def test_deploy_script_decoupled_from_game_server_update_flags():
    """Ensure deploy.sh does not touch game server update request flags.

    Regression test for Issue #32: Git safe.directory ownership handling, exit trap reporting,
    and UI step harmonization.
    """
    deploy_sh = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
    content = deploy_sh.read_text(encoding="utf-8")

    # Ensure deploy.sh does not touch game server update request flags
    assert "/home/steam/.update_requested" not in content
    assert "/var/lib/palmanager/update_requested" not in content


def test_index_html_harmonized_steppers_and_no_database_migrations():
    """Verify index.html has harmonized update steppers and no obsolete database migrations.

    Regression test for Issue #32: Git safe.directory ownership handling, exit trap reporting,
    and UI step harmonization.
    """
    index_html = Path(__file__).resolve().parent.parent / "app" / "templates" / "index.html"
    assert index_html.is_file(), "index.html must exist"
    content = index_html.read_text(encoding="utf-8")

    # Ensure legacy nonexistent database migrations bullet is removed
    assert "database migrations" not in content.lower()

    # Ensure Tab 8 has accurate management plane nomenclature
    assert "System Updates &amp; Web Management Deployment" in content
    assert "Game server engine (PalServer) updates remain isolated" in content

    # Ensure #updateOverlay contains 5 canonical steps
    assert "1. Pull latest commits from upstream git origin" in content
    assert "2. Sync code tree &amp; systemd service units" in content
    assert "3. Enforce POSIX ACLs &amp; cross-user storage" in content
    assert "4. Install &amp; verify Python dependencies with uv" in content
    assert "5. Cleanly restart daemon &amp; recover portal connection" in content


def test_index_html_poll_deploy_progress_guards_failure_overlay():
    """Verify pollDeployProgress guards showUpdateOverlay with step 5 or pct check on failure.

    Regression test for Issue #32: Git safe.directory ownership handling, exit trap reporting,
    and UI step harmonization.
    """
    index_html = Path(__file__).resolve().parent.parent / "app" / "templates" / "index.html"
    content = index_html.read_text(encoding="utf-8")

    # Ensure pollDeployProgress guards showUpdateOverlay with step 5 or pct check
    assert "deployLastProg" in content
    assert "deployLastProg.current_step >= 5 || deployLastProg.percentage >= 90" in content
    assert "hasFailed" in content
    assert "Deployment failed:" in content
