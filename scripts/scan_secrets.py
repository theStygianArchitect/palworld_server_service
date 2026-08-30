"""Hardcoded Secret & Credential Scanner.

Scans all project files (Python, Shell, systemd unit files, HTML, Markdown, TOML, JSON)
for hardcoded API tokens, real Discord webhooks, DuckDNS credentials, private keys,
and unmasked passwords to ensure zero-leak secret hygiene.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Safe placeholder values permitted in test mocks or default fallbacks
SAFE_ALLOWLIST: set[str] = {
    "admin_password",
    "test_password",
    "your_duckdns_token",
    "your_subdomain",
    "yourdomain.duckdns.org",
    "https://discord.com/api/webhooks/12345/abcdef",
    "5b834ae0-822a-439f-b087-9ac42a16ac63",  # Mock UUID in test_logger.py
    "MySecretPassword123",  # Mock password string tested in test_logger.py
    "SecureAdminPassword123",  # Mock password in test_config.py
    "SecretPassword123",  # Mock password in test_config_parser.py / test_tracker.py
    "HackedPassword",  # Test injection string in test_config_pipeline
    "dXNlcjpwYXNz",  # Mock base64 tested in test_logger.py
    "[REDACTED]",
    "[REDACTED_UUID]",
}

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Live Discord Webhook Token",
        re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d{17,20}/[A-Za-z0-9_-]{60,}"),
    ),
    (
        "GitHub Personal Access Token",
        re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}"),
    ),
    (
        "Generic Private Key Block",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "AWS Access Key ID / Secret",
        re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),
    ),
    (
        "Hardcoded DuckDNS Live Token",
        re.compile(
            r"(?i)duckdns.*token\s*[:=]\s*[\"']?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})[\"']?"
        ),
    ),
    (
        "Hardcoded Admin / Join Password Assignment",
        re.compile(
            r'(?i)(?:admin_password|server_password|adminpassword|serverpassword)\s*[:=]\s*["\']([^"\'\s,]{8,})["\']'
        ),
    ),
]

IGNORED_DIRS: set[str] = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".gemini",
    "dist",
    "build",
}

IGNORED_FILES: set[str] = {
    "uv.lock",
}


def scan_file(file_path: Path) -> list[str]:
    """Scans a single file line-by-line for secret patterns.

    Args:
        file_path (Path): Target file to inspect.

    Returns:
        list[str]: Descriptions of detected secret violations.
    """
    violations: list[str] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except PermissionError as err:
        sys.stderr.write(f"Permission denied reading file {file_path}: {err}\n")
        return [f"{file_path}: Permission denied: {err}"]
    except OSError as err:
        sys.stderr.write(f"OS error reading file {file_path}: {err}\n")
        return [f"{file_path}: OS error: {err}"]

    for line_no, line in enumerate(content.splitlines(), start=1):
        clean_line = line.strip()
        if not clean_line or clean_line.startswith("#"):
            # Check comments too just in case secrets are commented out
            pass

        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                matched_str = match.group(0)
                # Check if matched content is explicitly in safe allowlist
                is_safe = False
                for safe_item in SAFE_ALLOWLIST:
                    if safe_item in matched_str or matched_str in safe_item:
                        is_safe = True
                        break

                if not is_safe:
                    violations.append(
                        f"{file_path}:{line_no} -> [{label}] Potential hardcoded secret found: '{matched_str[:4]}...{matched_str[-4:]}'"
                    )

    return violations


def main() -> int:
    """Scans all repository files and exits non-zero if any secret is detected.

    Returns:
        int: Exit code 0 if clean, 1 if secrets found.
    """
    root_dir = Path.cwd()
    all_violations: list[str] = []
    scanned_count = 0

    for path in root_dir.rglob("*"):
        if path.is_file():
            # Skip ignored directories and files
            if any(part in IGNORED_DIRS for part in path.parts) or path.name in IGNORED_FILES:
                continue

            scanned_count += 1
            all_violations.extend(scan_file(path))

    print(f"[*] Scanned {scanned_count} project files for hardcoded secrets, tokens, and keys.")

    if all_violations:
        print("[-] Secret Scanner Detected Potential Hardcoded Secrets:")
        for v in all_violations:
            print(f"  - {v}")
        return 1

    print("[+] Secret Scanner Passed: 0 hardcoded credentials or tokens found across all files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
