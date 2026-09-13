"""Static file contract tests for repository governance, CI workflows, and safety defaults."""
# pylint: disable=missing-function-docstring,redefined-outer-name
# Rationale: Standard pytest idioms with fixtures and self-describing test functions.

import re
from pathlib import Path

import yaml

import app
from app.api.schemas import GameplaySettingsSchema
from app.config_manager.parser import serialize_ini_settings


def test_issue_3_pr_template_contract():
    """Validates pull request template structure, 3 AM checklist, and Defect-Driven Testing gate."""
    repo_root = Path(__file__).resolve().parent.parent
    pr_template_path = repo_root / ".github" / "pull_request_template.md"
    assert pr_template_path.exists(), f"PR template not found at {pr_template_path}"

    content = pr_template_path.read_text(encoding="utf-8")
    assert "TARGET BRANCH POLICY" in content
    assert "Motivation and Context" in content
    assert "12-Factor & 3 AM Resilience Checklist" in content
    assert "Checklist:" in content

    # Defect-Driven Testing gate
    normalized_content = content.replace("`", "")
    assert (
        "Added an automated regression test in tests/" in content
        or "added an automated regression test in tests/" in normalized_content.lower()
    ), "Defect-Driven Testing regression test gate missing from PR template"


def test_issue_4_ci_workflow_contract():
    """Validates CI workflow YAML structure, quality gate jobs, and multi-Python matrix."""
    repo_root = Path(__file__).resolve().parent.parent
    ci_workflow_path = repo_root / ".github" / "workflows" / "ci.yml"
    assert ci_workflow_path.exists(), f"CI workflow not found at {ci_workflow_path}"

    content = ci_workflow_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict), "Parsed CI workflow YAML must be a dictionary"
    assert "jobs" in data, "'jobs' section missing from CI workflow"

    jobs = data["jobs"]
    assert isinstance(jobs, dict), "'jobs' in CI workflow must be a mapping of job IDs"

    required_gate_ids = [
        "security-scan",
        "dep-audit",
        "pycodestyle",
        "docstrings",
        "ruff",
        "bandit",
        "pylint",
        "clean-install",
        "ast-audit",
        "suppression-audit",
        "mypy",
        "matrix",
        "package-build",
        "all-passed",
    ]

    job_id_aliases: dict[str, set[str]] = {
        "security-scan": {"security-scan", "secret-scan"},
        "dep-audit": {"dep-audit", "pip-audit"},
        "pycodestyle": {"pycodestyle"},
        "docstrings": {"docstrings", "pydocstyle"},
        "ruff": {"ruff"},
        "bandit": {"bandit"},
        "pylint": {"pylint"},
        "clean-install": {"clean-install", "idempotent-install"},
        "ast-audit": {"ast-audit", "ast-exception-audit"},
        "suppression-audit": {"suppression-audit"},
        "mypy": {"mypy"},
        "matrix": {"matrix", "python-matrix"},
        "package-build": {"package-build", "uv-build", "build"},
        "all-passed": {"all-passed", "ci-gate"},
    }

    for gate_id in required_gate_ids:
        aliases = job_id_aliases.get(gate_id, {gate_id})
        assert any(alias in jobs for alias in aliases), (
            f"Required quality gate job '{gate_id}' (aliases: {aliases}) not found in CI jobs: {list(jobs.keys())}"
        )

    matrix_job = jobs.get("matrix") or jobs.get("python-matrix")
    assert matrix_job is not None, "Python matrix job ('matrix' or 'python-matrix') not found in CI workflow"
    strategy = matrix_job.get("strategy", {})
    matrix = strategy.get("matrix", {})
    python_versions = matrix.get("python-version", [])
    expected_versions = ["3.10", "3.11", "3.12", "3.13"]
    for expected_ver in expected_versions:
        assert expected_ver in python_versions, f"Python version {expected_ver} missing from matrix: {python_versions}"


def test_issue_13_negative_rcon_disabled_by_default():
    """Negative regression test for Issue #13: insecure RCON disabled by default."""
    schema = GameplaySettingsSchema()
    assert schema.RCONEnabled is False

    repo_root = Path(__file__).resolve().parent.parent
    ini_candidates = [
        repo_root / "PalWorldSettings.ini",
        repo_root / "PalWorldSettings.ini.example",
        repo_root / "PalWorldSettings.ini.sample",
    ]
    for ini_path in ini_candidates:
        if ini_path.exists():
            content = ini_path.read_text(encoding="utf-8")
            assert "RCONEnabled=False" in content or "RCONEnabled=false" in content, (
                f"RCONEnabled must be False in {ini_path}"
            )

    # Verify that serialized default gameplay configuration disables RCON by default
    sample_ini = serialize_ini_settings(schema.model_dump(exclude_none=True))
    assert "RCONEnabled=False" in sample_ini or "RCONEnabled=false" in sample_ini, (
        "Default serialized INI must specify RCONEnabled=False"
    )


def test_issue_36_version_and_changelog_contract():
    """Validates Issue #36: root CHANGELOG.md exists, unreleased/0.2.0 sections, and pyproject matches __version__."""
    repo_root = Path(__file__).resolve().parent.parent
    changelog_path = repo_root / "CHANGELOG.md"
    assert changelog_path.exists(), f"CHANGELOG.md not found at {changelog_path}"

    content = changelog_path.read_text(encoding="utf-8")
    assert "## [Unreleased]" in content, "CHANGELOG.md must contain '## [Unreleased]'"
    assert "## [0.2.0]" in content, "CHANGELOG.md must contain '## [0.2.0]'"

    # pyproject.toml version matches app.__version__ and is 0.2.0
    pyproject_path = repo_root / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found at {pyproject_path}"

    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pyproject_text, re.MULTILINE)
    assert match is not None, "version string not found in pyproject.toml"
    pyproject_version = match.group(1)

    assert app.__version__ == "0.2.0", f"app.__version__ must be '0.2.0', got '{app.__version__}'"
    assert pyproject_version == "0.2.0", f"pyproject.toml version must be '0.2.0', got '{pyproject_version}'"
    assert pyproject_version == app.__version__, (
        f"pyproject.toml version ({pyproject_version}) does not match app.__version__ ({app.__version__})"
    )


def test_issue_38_deploy_script_atomic_staging_contract() -> None:
    """Validates Issue #38: deploy.sh wraps execution in main() and uses atomic staging for self-updates."""
    repo_root = Path(__file__).resolve().parent.parent
    deploy_script_path = repo_root / "scripts" / "deploy.sh"
    assert deploy_script_path.exists(), f"deploy.sh not found at {deploy_script_path}"

    content = deploy_script_path.read_text(encoding="utf-8")
    assert "main() {" in content, "deploy.sh must declare 'main() {' to ensure full AST memory parsing"
    assert 'main "$@"' in content, "deploy.sh must invoke 'main \"$@\"'"

    # Atomic staging assertions
    assert "deploy.sh.tmp" in content, "deploy.sh must use 'deploy.sh.tmp' staging"
    assert "mv -f" in content, "deploy.sh must use atomic 'mv -f' to replace deploy.sh"
    assert 'mv -f "${APP_DIR}/scripts/deploy.sh.tmp" "${APP_DIR}/scripts/deploy.sh"' in content, (
        "deploy.sh must atomically mv deploy.sh.tmp into deploy.sh"
    )


def test_issue_20_atomic_config_persistence_contract() -> None:
    """Validates Issue #20: atomic I/O engine exists and public API contracts are callable."""
    repo_root = Path(__file__).resolve().parent.parent
    atomic_io_path = repo_root / "app" / "core" / "atomic_io.py"
    assert atomic_io_path.exists(), f"Atomic I/O module not found at {atomic_io_path}"

    # Import contract verification from app.core.atomic_io
    from app.core.atomic_io import (  # pylint: disable=import-outside-toplevel
        AtomicStagingContext,
        atomic_write_file,
        atomic_write_ini,
    )

    assert callable(atomic_write_file), "atomic_write_file must be callable"
    assert callable(atomic_write_ini), "atomic_write_ini must be callable"
    assert callable(AtomicStagingContext), "AtomicStagingContext must be callable"

    # Import contract verification from app.config_manager.pipeline
    from app.config_manager.pipeline import (  # pylint: disable=import-outside-toplevel
        atomic_write_ini as pipeline_atomic_write_ini,
    )

    assert callable(pipeline_atomic_write_ini), "pipeline.atomic_write_ini must be callable"


def test_issue_43_deploy_runner_isolation_contract() -> None:
    """Validates Issue #43: deploy runner isolation and DEPLOY_REEXEC re-exec guard."""
    repo_root = Path(__file__).resolve().parent.parent

    # 1. Assert app/engine/updater.py references isolated deploy runner
    updater_path = repo_root / "app" / "engine" / "updater.py"
    assert updater_path.exists(), f"updater.py not found at {updater_path}"
    updater_content = updater_path.read_text(encoding="utf-8")
    assert "palmanager_deploy_runner.sh" in updater_content, (
        "app/engine/updater.py must reference 'palmanager_deploy_runner.sh'"
    )

    # 2. Assert scripts/deploy.sh contains DEPLOY_REEXEC guard and out-of-tree runner pattern
    deploy_script_path = repo_root / "scripts" / "deploy.sh"
    assert deploy_script_path.exists(), f"deploy.sh not found at {deploy_script_path}"
    deploy_content = deploy_script_path.read_text(encoding="utf-8")
    assert "DEPLOY_REEXEC" in deploy_content, "scripts/deploy.sh must contain 'DEPLOY_REEXEC' guard"
    assert "palmanager_deploy_runner" in deploy_content, (
        "scripts/deploy.sh must reference out-of-tree runner pattern 'palmanager_deploy_runner'"
    )


def test_issue_40_canonical_navigation_contract() -> None:
    """Validates Issue #40: Header branding navigation anchor and canonical URL schemas."""
    repo_root = Path(__file__).resolve().parent.parent

    # 1. Assert template contains canonical anchor
    template_path = repo_root / "app" / "templates" / "index.html"
    assert template_path.exists(), f"index.html not found at {template_path}"
    template_content = template_path.read_text(encoding="utf-8")
    assert 'id="headerSuiteLogoLink"' in template_content, (
        "index.html must contain header navigation anchor 'headerSuiteLogoLink'"
    )
    assert "https://thestygianarchitect.duckdns.org:8080" in template_content, (
        "index.html must default to canonical URL 'https://thestygianarchitect.duckdns.org:8080'"
    )

    # 2. Assert schemas expose canonical_url
    from app.api.schemas import (  # pylint: disable=import-outside-toplevel
        SystemVersionResponse,
        TLSStatusResponse,
    )

    assert "canonical_url" in dict(SystemVersionResponse.model_fields)
    assert "canonical_url" in dict(TLSStatusResponse.model_fields)
