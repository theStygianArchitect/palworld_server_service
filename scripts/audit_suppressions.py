"""Project-Wide Suppression Auditor.

Validates that no project-wide lint, type, or security suppressions exist in
configuration files (such as pyproject.toml) without explicit user authorization
demonstrated by the exact passphrase: "I solemnly swear I know what I'm doing".
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError as err:
    sys.stderr.write(f"Notice: tomllib stdlib not available ({err}), attempting tomli fallback.\n")
    import tomli as tomllib  # type: ignore[no-redef]

USER_OVERRIDE_STATEMENT = "I solemnly swear I know what I\u2019m doing"
USER_OVERRIDE_STATEMENT_ASCII = "I solemnly swear I know what I'm doing"


def get_commit_message() -> str:
    """Attempts to retrieve the latest Git commit message.

    Returns:
        str: Commit message or empty string on failure.
    """
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except subprocess.SubprocessError as err:
        sys.stderr.write(f"Warning: Git command failed retrieving commit message: {err}\n")
    except OSError as err:
        sys.stderr.write(f"Warning: OS error retrieving commit message: {err}\n")
    return ""


def is_passphrase_authorized() -> bool:
    """Checks whether the explicit user authorization statement is provided.

    Returns:
        bool: True if authorized via environment variable or commit message.
    """
    env_phrase = os.environ.get("PROJECT_WIDE_OVERRIDE_STATEMENT", "").strip()
    if env_phrase in (USER_OVERRIDE_STATEMENT, USER_OVERRIDE_STATEMENT_ASCII):
        return True

    commit_msg = get_commit_message()
    return bool(USER_OVERRIDE_STATEMENT in commit_msg or USER_OVERRIDE_STATEMENT_ASCII in commit_msg)


def audit_pyproject_toml(config_path: Path) -> list[str]:
    """Audits pyproject.toml for unauthorized project-wide rule suppressions.

    Args:
        config_path (Path): Path to pyproject.toml.

    Returns:
        list[str]: List of violation descriptions.
    """
    violations: list[str] = []
    if not config_path.exists():
        return violations

    try:
        content = config_path.read_text(encoding="utf-8")
        data = tomllib.loads(content)
    except PermissionError as err:
        sys.stderr.write(f"Permission denied reading {config_path}: {err}\n")
        return [f"Permission denied reading {config_path}: {err}"]
    except OSError as err:
        sys.stderr.write(f"OS error reading {config_path}: {err}\n")
        return [f"OS error reading {config_path}: {err}"]
    except tomllib.TOMLDecodeError as err:
        sys.stderr.write(f"TOML decode error in {config_path}: {err}\n")
        return [f"TOML decode error in {config_path}: {err}"]

    # 1. Check PyLint disabled messages
    tool_section = data.get("tool", {})
    pylint_section = tool_section.get("pylint", {})
    messages_control = pylint_section.get("messages control", {})
    disabled_rules = messages_control.get("disable", [])

    if disabled_rules:
        violations.append(
            f"[PYLINT SUPPRESSION] pyproject.toml contains project-wide disable rules: {disabled_rules}"
        )

    # 2. Check Ruff ignored rules
    ruff_section = tool_section.get("ruff", {})
    ruff_lint = ruff_section.get("lint", {})
    ignored_rules = ruff_lint.get("ignore", [])
    if ignored_rules:
        violations.append(
            f"[RUFF SUPPRESSION] pyproject.toml contains project-wide ignore rules: {ignored_rules}"
        )

    # 3. Check Mypy ignore_errors or broad ignores
    mypy_section = tool_section.get("mypy", {})
    if mypy_section.get("ignore_errors", False):
        violations.append("[MYPY SUPPRESSION] pyproject.toml has 'ignore_errors = true'")

    return violations


def main() -> int:
    """Main CLI entrypoint for project-wide suppression auditing.

    Returns:
        int: 0 if compliant or authorized, 1 if unauthorized suppressions exist.
    """
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_path = repo_root / "pyproject.toml"

    violations = audit_pyproject_toml(pyproject_path)

    if not violations:
        sys.stdout.write("[+] Passed project-wide suppression audit (zero suppressions detected).\n")
        return 0

    # Violations exist: check for explicit passphrase authorization
    if is_passphrase_authorized():
        sys.stdout.write(
            "[!] Project-wide suppressions detected, but authorized by user passphrase: "
            "'I solemnly swear I know what I'm doing'.\n"
        )
        for v in violations:
            sys.stdout.write(f"  - {v}\n")
        return 0

    sys.stderr.write("=========================================================================\n")
    sys.stderr.write("[-] CRITICAL REPOSITORY QUALITY VIOLATION:\n")
    sys.stderr.write("    Project-wide suppressions detected without explicit user authorization!\n")
    sys.stderr.write("=========================================================================\n")
    for v in violations:
        sys.stderr.write(f"  [-] {v}\n")
    sys.stderr.write("\nTo authorize project-wide suppressions, the user must explicitly provide:\n")
    sys.stderr.write("    \"I solemnly swear I know what I'm doing\"\n")
    sys.stderr.write("via environment variable PROJECT_WIDE_OVERRIDE_PASSPHRASE or commit message.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
