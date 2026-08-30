import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from .config_parser import parse_ini_file, serialize_ini_settings
from .config_pipeline import PROTECTED_ADMIN_KEYS


class IsolatedGitBackupManager:
    def __init__(self, live_ini_path: str, backup_branch: str = "config-snapshots"):
        self.live_ini_path = Path(live_ini_path).resolve()
        self.backup_repo_dir = (
            Path("/var/lib/palmanager/backups").resolve()
            if os.name != "nt"
            else (Path.home() / ".palmanager" / "backups").resolve()
        )
        self.backup_branch = backup_branch
        self.staged_file = self.backup_repo_dir / "PalWorldSettings.ini"
        self._init_repo()

    def _run_git(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args,
            cwd=str(self.backup_repo_dir),
            capture_output=True,
            text=True,
            check=check,
        )

    def _init_repo(self):
        self.backup_repo_dir.mkdir(parents=True, exist_ok=True)
        if not (self.backup_repo_dir / ".git").exists():
            self._run_git(["init", "-b", self.backup_branch])
            self._run_git(["config", "user.name", "PalworldDaemon"])
            self._run_git(["config", "user.email", "daemon@palworld.local"])
            self._run_git(["config", "commit.gpgsign", "false"])

    def create_commit(self, tag: str, message: str) -> Optional[str]:
        if not self.live_ini_path.exists():
            return None

        try:
            live_settings = parse_ini_file(str(self.live_ini_path))
        except Exception:
            return None

        redacted_settings = {}
        for k, v in live_settings.items():
            if k in PROTECTED_ADMIN_KEYS and v:
                redacted_settings[k] = "[REDACTED]"
            else:
                redacted_settings[k] = v

        serialized = serialize_ini_settings(redacted_settings)
        with open(self.staged_file, "w", encoding="utf-8") as f:
            f.write(serialized)

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

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
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
        commits = []
        for line in res.stdout.strip().split("\n"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return commits

    def get_diff(self, commit_hash: str) -> str:
        try:
            res = self._run_git(["show", "--color=never", commit_hash, "--", self.staged_file.name], check=False)
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""

    def restore_commit(self, commit_hash: str) -> bool:
        try:
            res = self._run_git(["checkout", commit_hash, "--", self.staged_file.name], check=False)
            if res.returncode != 0:
                return False
            historical_data = parse_ini_file(str(self.staged_file))
            live_full = parse_ini_file(str(self.live_ini_path)) if self.live_ini_path.exists() else {}

            for k, v in historical_data.items():
                if k not in PROTECTED_ADMIN_KEYS and v != "[REDACTED]":
                    live_full[k] = v

            temp_target = str(self.live_ini_path) + ".tmp"
            self.live_ini_path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_target, "w", encoding="utf-8") as f:
                f.write(serialize_ini_settings(live_full))
            os.replace(temp_target, self.live_ini_path)

            self._run_git(["checkout", self.backup_branch, "--", self.staged_file.name], check=False)
            self.create_commit("REVERT", f"Restored configuration from commit {commit_hash}")
            return True
        except Exception:
            return False
