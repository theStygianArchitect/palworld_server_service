"""Static file contract tests for repository governance, CI workflows, and safety defaults."""
# pylint: disable=missing-function-docstring,redefined-outer-name
# Rationale: Standard pytest idioms with fixtures and self-describing test functions.

from pathlib import Path

import yaml

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
