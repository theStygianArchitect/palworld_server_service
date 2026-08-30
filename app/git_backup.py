"""Isolated Git configuration versioning and snapshot manager.

Maintains an isolated Git repository tracking historical revisions of PalWorldSettings.ini,
enabling diff inspections and atomic rollbacks with administrative credential redaction.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config_parser import parse_ini_file, serialize_ini_settings
from .config_pipeline import PROTECTED_ADMIN_KEYS
from .logger import log
from .types import GitCommitInfo


class IsolatedGitBackupManager:
    """Manages an isolated Git repository for versioning server configuration snapshots.

    Attributes:
        live_ini_path (Path): Resolved path to active live PalWorldSettings.ini.
        backup_repo_dir (Path): Resolved path to isolated repository root directory.
        backup_branch (str): Branch name used for configuration snapshot history.
        staged_file (Path): Path to the staged INI copy within the backup repository.
    """

    def __init__(
        self,
        live_ini_path: str | Path,
        backup_repo_dir: str | Path | None = None,
        backup_branch: str = "config-snapshots",
    ) -> None:
        """Initializes the Git backup repository manager.

        Args:
            live_ini_path (str | Path): Filepath to live PalWorldSettings.ini.
            backup_repo_dir (str | Path | None): Directory path for isolated Git backup repository.
            backup_branch (str): Branch name for configuration commits (default: 'config-snapshots').
        """
        self.live_ini_path: Path = Path(live_ini_path).resolve()
        if backup_repo_dir is not None:
            self.backup_repo_dir: Path = Path(backup_repo_dir).resolve()
        else:
            default_dir = (
                Path("/var/lib/palmanager/backups").resolve()
                if os.name != "nt"
                else (Path.home() / ".palmanager" / "backups").resolve()
            )
            try:
                default_dir.mkdir(parents=True, exist_ok=True)
                self.backup_repo_dir = default_dir
            except (PermissionError, OSError):
                self.backup_repo_dir = (Path.home() / ".palmanager" / "backups").resolve()

        self.backup_branch: str = backup_branch
        self.staged_file: Path = self.backup_repo_dir / "PalWorldSettings.ini"
        self._init_repo()

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        """Executes a Git CLI command within the backup repository directory.

        Args:
            args (list[str]): Subcommand arguments passed to git.
            check (bool): Whether to raise CalledProcessError on non-zero exit codes.

        Returns:
            subprocess.CompletedProcess[str]: Completed process result containing stdout and stderr.
        """
        git_bin = shutil.which("git") or "git"
        return subprocess.run(
            [git_bin] + args,
            cwd=str(self.backup_repo_dir),
            capture_output=True,
            text=True,
            check=check,
        )

    def _init_repo(self) -> None:
        """Initializes repository directory and Git configuration if not present."""
        try:
            self.backup_repo_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as err:
            log.warning(
                "Permission denied initializing backup repo at %s: %s. Falling back to home directory.",
                self.backup_repo_dir,
                err,
            )
            self.backup_repo_dir = (Path.home() / ".palmanager" / "backups").resolve()
            self.staged_file = self.backup_repo_dir / "PalWorldSettings.ini"
            self.backup_repo_dir.mkdir(parents=True, exist_ok=True)

        if not (self.backup_repo_dir / ".git").exists():
            self._run_git(["init", "-b", self.backup_branch])
            self._run_git(["config", "user.name", "PalworldDaemon"])
            self._run_git(["config", "user.email", "daemon@palworld.local"])
            self._run_git(["config", "commit.gpgsign", "false"])

    def create_commit(self, tag: str, message: str) -> str | None:
        """Creates a redacted Git commit capturing the current state of PalWorldSettings.ini.

        Args:
            tag (str): Commit category tag (e.g. 'SAVE', 'BOOT', 'REVERT').
            message (str): Human-readable description of configuration change.

        Returns:
            str | None: Short Git commit hash string, or None if snapshot failed.
        """
        if not self.live_ini_path.exists():
            return None

        try:
            live_settings = parse_ini_file(self.live_ini_path)
        except Exception as err:
            log.debug("Failed parsing live settings for commit: %s", err)
            return None

        redacted_settings: dict[str, Any] = {}
        for k, v in live_settings.items():
            if k in PROTECTED_ADMIN_KEYS and v:
                redacted_settings[k] = "[REDACTED]"
            else:
                redacted_settings[k] = v

        serialized = serialize_ini_settings(redacted_settings)
        try:
            self.staged_file.write_text(serialized, encoding="utf-8")
        except PermissionError as err:
            log.warning("Permission denied writing staged file %s: %s", self.staged_file, err)
            return None
        except OSError as err:
            log.warning("OS error writing staged file %s: %s", self.staged_file, err)
            return None

        self._run_git(["add", self.staged_file.name])
        status = self._run_git(["status", "--porcelain"])
        if not status.stdout.strip():
            # If nothing changed, return latest commit hash
            rev = self._run_git(["rev-parse", "--short", "HEAD"], check=False)
            return rev.stdout.strip() if rev.returncode == 0 else None

        commit_msg = f"[{tag.upper()}] {message}"
        self._run_git(["commit", "-m", commit_msg])
        rev = self._run_git(["rev-parse", "--short", "HEAD"])
        return rev.stdout.strip()

    def get_history(self, limit: int = 50) -> list[GitCommitInfo]:
        """Retrieves list of recent Git snapshot commits from repository log.

        Args:
            limit (int): Maximum number of log history entries to fetch (default: 50).

        Returns:
            list[GitCommitInfo]: Chronological list of snapshot commit records.
        """
        log_format = "%h|%an|%ad|%s"
        res = self._run_git(
            [
                "log",
                f"-n{limit}",
                f"--pretty=format:{log_format}",
                "--date=iso",
                "--",
                self.staged_file.name,
            ],
            check=False,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return []
        commits: list[GitCommitInfo] = []
        for line in res.stdout.strip().split("\n"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(
                    {
                        "hash": parts[0],
                        "message": parts[3],
                        "date": parts[2],
                    }
                )
        return commits

    def get_diff(self, commit_hash: str) -> str:
        """Generates unified diff between a target commit and its immediate parent.

        Args:
            commit_hash (str): Target Git commit hash to inspect.

        Returns:
            str: Unified diff text string.
        """
        if not re.match(r"^[a-fA-F0-9]{4,64}$", commit_hash):
            log.warning("Invalid commit hash rejected for diff: %s", commit_hash)
            return ""

        try:
            res = self._run_git(["show", "--color=never", commit_hash, "--", self.staged_file.name], check=False)
            return res.stdout if res.returncode == 0 else ""
        except subprocess.SubprocessError as err:
            log.debug("Git diff failed for %s: %s", commit_hash, err)
            return ""
        except OSError as err:
            log.debug("Git execution error for %s: %s", commit_hash, err)
            return ""

    def restore_commit(self, commit_hash: str) -> bool:
        """Restores public gameplay settings from historical commit while preserving live admin secrets.

        Args:
            commit_hash (str): Target snapshot commit hash to roll back to.

        Returns:
            bool: True if rollback was successfully merged and persisted to disk.
        """
        if not re.match(r"^[a-fA-F0-9]{4,64}$", commit_hash):
            log.warning("Invalid commit hash rejected for restore: %s", commit_hash)
            return False

        try:
            res = self._run_git(["checkout", commit_hash, "--", self.staged_file.name], check=False)
            if res.returncode != 0:
                return False
            historical_data = parse_ini_file(self.staged_file)
            live_full = parse_ini_file(self.live_ini_path) if self.live_ini_path.exists() else {}

            for k, v in historical_data.items():
                if k not in PROTECTED_ADMIN_KEYS and v != "[REDACTED]":
                    live_full[k] = v

            temp_target = self.live_ini_path.with_suffix(".tmp")
            self.live_ini_path.parent.mkdir(parents=True, exist_ok=True)
            temp_target.write_text(serialize_ini_settings(live_full), encoding="utf-8")
            temp_target.replace(self.live_ini_path)

            self._run_git(["checkout", self.backup_branch, "--", self.staged_file.name], check=False)
            self.create_commit("REVERT", f"Restored configuration from commit {commit_hash}")
            return True
        except subprocess.SubprocessError as err:
            log.warning("Git error during restore of commit %s: %s", commit_hash, err)
            return False
        except PermissionError as err:
            log.warning("Permission denied writing restored configuration: %s", err)
            return False
        except OSError as err:
            log.warning("OS error during restore of commit %s: %s", commit_hash, err)
            return False
