"""Upstream repository change watcher and automated deployment orchestrator.

Provides asynchronous polling of remote GitHub releases and commits, telemetry caching,
mutual-exclusion deployment locking, and detached zero-drift host execution.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import shutil
import subprocess  # nosec B404 - required for detached deploy.sh execution and git commit resolution
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from app.api.schemas import (
    DeploymentProgressResponse,
    DeploymentStepInfo,
    PostUpdateSummary,
    UpdateApplyResponse,
    UpdateStatusResponse,
)
from app.core.logger import log

DEFAULT_UPDATE_LOCK_FILE: Path = Path(tempfile.gettempdir()) / "palmanager_update.lock"
DEFAULT_POST_UPDATE_FILE: Path = Path("/var/lib/palmanager/last_update.json")
STALE_LOCK_TIMEOUT_SECONDS: float = 1800.0  # 30 minutes


def _resolve_default_deploy_log_path() -> Path:
    """Returns a writable destination path for deploy script execution logs."""
    if os.name == "nt":
        log_dir = Path.home() / ".palmanager" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "deploy.log"

    var_log = Path("/var/log/palmanager")
    if var_log.is_dir() and os.access(var_log, os.W_OK):
        return var_log / "deploy.log"

    fallback_dir = Path.home() / ".palmanager" / "logs"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    return fallback_dir / "deploy.log"


def _resolve_post_update_file_path(repo_dir: Path | None = None) -> Path:
    """Returns the readable destination path for post-update summary record."""
    candidates = [
        Path("/var/lib/palmanager/last_update.json"),
        Path.home() / ".palmanager" / "last_update.json",
    ]
    if repo_dir:
        candidates.append(repo_dir / "last_update.json")

    for cand in candidates:
        if cand.is_file():
            return cand
    if os.name == "nt":
        log_dir = Path.home() / ".palmanager"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "last_update.json"
    return candidates[0]


def _spawn_detached_deployer(deploy_script: Path, target_branch: str) -> None:
    """Spawns deploy.sh in a detached session redirected to deployment log.

    Args:
        deploy_script: Absolute path to deploy.sh.
        target_branch: Git branch name to checkout and deploy.
    """
    deploy_log_path = _resolve_default_deploy_log_path()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    # Open log file handle for non-blocking detached process redirect
    # pylint: disable=consider-using-with
    log_fd = open(deploy_log_path, "a", encoding="utf-8")  # noqa: SIM115
    log_fd.write(f"\n--- Update Deployment Triggered at {now_dt} ---\n")
    log_fd.flush()

    if os.name == "posix":
        # sudo(8) must run as root to restart systemd service after deployment.
        # The deploy.sh script path is validated as an existing file before reaching this call.
        # start_new_session=True ensures the child process outlives the parent web process restart.
        sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"  # nosec B607 - absolute path resolved
        subprocess.Popen(  # nosec B603 - argument list is validated; no shell=True; setuid binary required
            [sudo_bin, "-n", str(deploy_script), target_branch],
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    else:
        bash_bin = shutil.which("bash") or "bash"  # nosec B607 - absolute path resolved via shutil.which
        subprocess.Popen(  # nosec B603 - argument list is validated; no shell=True; Windows dev-only path
            [bash_bin, str(deploy_script), target_branch],
            stdout=log_fd,
            stderr=subprocess.STDOUT,
        )


class UpdateWatcher:
    """Monitors upstream GitHub commits and orchestrates host update deployments.

    Attributes:
        repo_url (str): Upstream GitHub repository web URL.
        branch (str): Target tracking branch name (e.g. 'main').
        check_interval_seconds (int): Polling frequency in seconds.
        deploy_script (Path | None): Explicit path to deploy.sh if configured.
        lock_file (Path): Filesystem mutex file path preventing concurrent deploys.
        repo_dir (Path): Local workspace repository root directory.
        command_runner (Callable | None): Injectable runner hook for unit testing.
    """

    # pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments
    # Rationale: State tracking aggregates telemetry cache, lock primitives, and GitHub metadata.
    def __init__(
        self,
        repo_url: str,
        branch: str = "main",
        check_interval_seconds: int = 600,
        deploy_script: Path | str | None = None,
        lock_file: Path | str | None = None,
        repo_dir: Path | str | None = None,
        command_runner: Callable[..., Any] | None = None,
        post_update_file: Path | str | None = None,
        deploy_log_path: Path | str | None = None,
    ) -> None:
        """Initializes the UpdateWatcher service.

        Args:
            repo_url: Upstream GitHub repository URL.
            branch: Git branch to track for updates.
            check_interval_seconds: Periodic probe interval in seconds.
            deploy_script: Optional override path to deploy.sh.
            lock_file: Optional override path to mutual exclusion lock file.
            repo_dir: Local repository directory root.
            command_runner: Optional test hook overriding detached subprocess dispatch.
            post_update_file: Optional override path to post-update metadata JSON file.
            deploy_log_path: Optional override path to deploy.log execution log file.
        """
        self.repo_url = repo_url
        self.branch = branch
        self.check_interval_seconds = check_interval_seconds
        self.deploy_script = Path(deploy_script) if deploy_script else None
        self.lock_file = Path(lock_file) if lock_file else DEFAULT_UPDATE_LOCK_FILE
        self.repo_dir = Path(repo_dir) if repo_dir else Path(__file__).parent.parent.parent
        self.command_runner = command_runner
        self.post_update_file = (
            Path(post_update_file) if post_update_file else _resolve_post_update_file_path(self.repo_dir)
        )
        self.deploy_log_path = Path(deploy_log_path) if deploy_log_path else None

        self._async_lock = asyncio.Lock()
        self._update_available: bool = False
        self._current_commit: str = ""
        self._latest_commit: str = ""
        self._commits_behind: int = 0
        self._latest_commit_message: str = ""
        self._last_checked: str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._last_error: str | None = None

    @staticmethod
    def parse_repo_slug(url: str) -> tuple[str, str]:
        """Extracts (owner, repo) tuple from a GitHub web URL.

        Args:
            url: GitHub repository URL string.

        Returns:
            tuple[str, str]: Repository owner and repository name.

        Raises:
            ValueError: If repository URL is invalid or malformed.
        """
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 2:
            raise ValueError(f"Invalid GitHub repository URL format: {url}")
        owner = path_parts[0]
        repo = path_parts[1].removesuffix(".git")
        return owner, repo

    def get_local_commit(self) -> str:
        """Resolves active deployed git commit SHA using git, metadata files, or fallbacks.

        Returns:
            str: Hexadecimal git commit SHA or 'unknown'.
        """
        # 1. Probe local git repository if .git directory is present
        git_dir = self.repo_dir / ".git"
        if git_dir.exists():
            try:
                # git(1) is the only reliable way to query the HEAD commit of a live worktree.
                # Using absolute path resolved via shutil.which eliminates B607 partial path risk.
                git_bin = shutil.which("git") or "git"  # nosec B607 - resolved via PATH
                proc = subprocess.run(  # nosec B603 - validated arg list; no shell=True; git is a trusted binary
                    [git_bin, "rev-parse", "HEAD"],
                    cwd=str(self.repo_dir),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5.0,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip()
            except subprocess.SubprocessError as err:
                log.debug("Git rev-parse subprocess probe failed in repo_dir %s: %s", self.repo_dir, err)
            except OSError as err:
                log.debug("Git rev-parse OS probe failed in repo_dir %s: %s", self.repo_dir, err)

        # 2. Check for deployed commit metadata file (.git_commit)
        candidate_meta_files = [
            self.repo_dir / ".git_commit",
            Path("/opt/palworld-web-manager/.git_commit"),
        ]
        for meta_file in candidate_meta_files:
            if meta_file.is_file():
                try:
                    content = meta_file.read_text(encoding="utf-8").strip()
                    if content:
                        return content
                except OSError as err:
                    log.debug("Error reading git commit metadata file %s: %s", meta_file, err)

        return "unknown"

    def is_update_in_progress(self) -> bool:
        """Evaluates whether an update deployment process is currently holding the lock file.

        Returns:
            bool: True if deployment is active, False if unlocked or stale.
        """
        if not self.lock_file.exists():
            return False

        try:
            mtime = self.lock_file.stat().st_mtime
            age = time.time() - mtime
            if age > STALE_LOCK_TIMEOUT_SECONDS:
                log.warning(
                    "Purging stale update lock file (%s, age: %.1fs > threshold: %.1fs)",
                    self.lock_file,
                    age,
                    STALE_LOCK_TIMEOUT_SECONDS,
                )
                self.lock_file.unlink(missing_ok=True)
                return False
            return True
        except OSError as err:
            log.warning("Error inspecting update lock file status: %s", err)
            return False

    def resolve_deploy_script(self) -> Path:
        """Discovers the absolute path to deploy.sh across known production and source layouts.

        Returns:
            Path: Discovered path object to deploy.sh.
        """
        if self.deploy_script and self.deploy_script.exists():
            return self.deploy_script

        candidates = [
            Path("/opt/palworld-web-manager/scripts/deploy.sh"),
            self.repo_dir / "scripts" / "deploy.sh",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate

        return candidates[0]

    # pylint: disable=too-many-branches,too-many-statements,too-many-locals
    # Rationale: GitHub REST API polling orchestrates commit lookup, compare diffs, and network error handling.
    async def check_for_updates(self) -> UpdateStatusResponse:
        """Queries upstream GitHub API to compare local deployed commit with remote HEAD.

        Returns:
            UpdateStatusResponse: Structured status containing update availability.
        """
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._last_checked = now_ts

        local_sha = self.get_local_commit()
        self._current_commit = local_sha

        try:
            owner, repo = self.parse_repo_slug(self.repo_url)
        except ValueError as err:
            log.warning("Failed parsing repository slug from %s: %s", self.repo_url, err)
            self._last_error = str(err)
            return self.get_status()

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Palworld-Update-Watcher/1.0",
        }

        commits_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{self.branch}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(commits_url, headers=headers)
                if resp.status_code in (403, 429):
                    log.warning("GitHub API rate limit exceeded during update check: HTTP %s", resp.status_code)
                    self._last_error = f"GitHub API rate limit encountered (HTTP {resp.status_code})"
                    return self.get_status()

                resp.raise_for_status()
                data = resp.json()

                remote_sha = str(data.get("sha", "")).strip()
                commit_meta = data.get("commit", {})
                message_full = commit_meta.get("message", "")
                summary_line = message_full.split("\n", 1)[0].strip() if message_full else ""

                self._latest_commit = remote_sha
                self._latest_commit_message = summary_line
                self._last_error = None

                # Determine commit divergence
                if local_sha != "unknown" and (
                    local_sha == remote_sha
                    or local_sha.startswith(remote_sha[:7])
                    or remote_sha.startswith(local_sha[:7])
                ):
                    self._update_available = False
                    self._commits_behind = 0
                    return self.get_status()

                if local_sha != "unknown" and local_sha and remote_sha:
                    # Query GitHub compare API to count commits behind
                    compare_url = f"https://api.github.com/repos/{owner}/{repo}/compare/{local_sha}...{remote_sha}"
                    compare_resp = await client.get(compare_url, headers=headers)
                    if compare_resp.status_code == 200:
                        compare_data = compare_resp.json()
                        ahead_by = int(compare_data.get("ahead_by", 1))
                        self._commits_behind = max(0, ahead_by)
                        self._update_available = self._commits_behind > 0
                    else:
                        log.debug(
                            "Compare API returned HTTP %s; defaulting commits_behind to 1",
                            compare_resp.status_code,
                        )
                        self._commits_behind = 1
                        self._update_available = True
                else:
                    self._commits_behind = 0
                    self._update_available = False

        except httpx.HTTPStatusError as err:
            log.warning("HTTP status error checking upstream updates: %s", err)
            self._last_error = f"HTTP {err.response.status_code} from upstream GitHub"
        except httpx.RequestError as err:
            log.warning("Network request error checking upstream updates: %s", err)
            self._last_error = "Network error connecting to GitHub API"
        except KeyError as err:
            log.warning("Missing key parsing GitHub API update payload: %s", err)
            self._last_error = f"Payload key error: {err}"
        except ValueError as err:
            log.warning("Value error parsing GitHub API update payload: %s", err)
            self._last_error = f"Payload value error: {err}"

        return self.get_status()

    def get_status(self) -> UpdateStatusResponse:
        """Returns the current cached update status DTO.

        Returns:
            UpdateStatusResponse: Populated update telemetry record.
        """
        return UpdateStatusResponse(
            update_available=self._update_available,
            current_commit=self._current_commit or "unknown",
            latest_commit=self._latest_commit or "unknown",
            target_branch=self.branch,
            commits_behind=self._commits_behind,
            latest_commit_message=self._latest_commit_message,
            last_checked=self._last_checked,
            update_in_progress=self.is_update_in_progress(),
            error=self._last_error,
            last_update=self.get_last_update_summary(),
        )

    def get_last_update_summary(self) -> PostUpdateSummary | None:
        """Inspects and returns the persisted post-update execution summary if present.

        Returns:
            PostUpdateSummary | None: Parsed summary DTO or None if unavailable/corrupted.
        """
        target = self.post_update_file
        if not target.is_file():
            target = _resolve_post_update_file_path(self.repo_dir)
            if not target.is_file():
                return None

        try:
            raw_text = target.read_text(encoding="utf-8").strip()
            if not raw_text:
                return None
            data = json.loads(raw_text)
            status_val: Literal["success", "failed"] = "failed" if data.get("status") == "failed" else "success"
            return PostUpdateSummary(
                status=status_val,
                target_branch=str(data.get("target_branch", "main")),
                deployed_commit=str(data.get("deployed_commit", "unknown")),
                deployed_commit_short=str(data.get("deployed_commit_short", "unknown")),
                deployed_at=str(data.get("deployed_at", "")),
                duration_seconds=max(0, int(data.get("duration_seconds", 0))),
                summary=str(data.get("summary", "")),
                acknowledged=bool(data.get("acknowledged", False)),
            )
        except OSError as err:
            log.warning("Filesystem error reading post-update summary from %s: %s", target, err)
            return None
        except json.JSONDecodeError as err:
            log.warning("JSON decode error in post-update summary from %s: %s", target, err)
            return None
        except ValueError as err:
            log.warning("Value error parsing post-update summary from %s: %s", target, err)
            return None

    def acknowledge_last_update(self) -> bool:
        """Marks the post-update summary record as acknowledged by the operator.

        Returns:
            bool: True if updated successfully, False otherwise.
        """
        target = self.post_update_file
        if not target.is_file():
            target = _resolve_post_update_file_path(self.repo_dir)
            if not target.is_file():
                return False

        try:
            raw_text = target.read_text(encoding="utf-8").strip()
            data = json.loads(raw_text)
            data["acknowledged"] = True
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
        except OSError as err:
            log.warning("Filesystem error acknowledging post-update summary at %s: %s", target, err)
            return False
        except json.JSONDecodeError as err:
            log.warning("JSON decode error acknowledging post-update summary at %s: %s", target, err)
            return False
        except ValueError as err:
            log.warning("Value error acknowledging post-update summary at %s: %s", target, err)
            return False

    # pylint: disable=too-many-branches,too-many-statements,too-many-locals
    # Rationale: Progression parsing aggregates lock metadata, multi-step log scanning, and ETA math.
    def get_deployment_progress(self, log_path: Path | None = None) -> DeploymentProgressResponse:
        """Parses active deployment progression, elapsed time, ETA, and tail logs.

        Args:
            log_path: Optional custom path to deployment log file.

        Returns:
            DeploymentProgressResponse: Live telemetry status.
        """
        is_active = self.is_update_in_progress()
        last_update = self.get_last_update_summary()

        default_steps = [
            DeploymentStepInfo(index=1, name="Pulling latest updates from origin", status="pending"),
            DeploymentStepInfo(index=2, name="Syncing application code & systemd units", status="pending"),
            DeploymentStepInfo(index=3, name="Enforcing POSIX ACLs & storage permissions", status="pending"),
            DeploymentStepInfo(index=4, name="Updating Python dependencies via uv", status="pending"),
            DeploymentStepInfo(index=5, name="Restarting palworld-manager.service", status="pending"),
        ]

        target_log = log_path or self.deploy_log_path or _resolve_default_deploy_log_path()
        log_lines: list[str] = []
        if target_log.is_file():
            try:
                with open(target_log, encoding="utf-8", errors="replace") as f:
                    log_lines = [line.rstrip() for line in f.readlines()[-30:]]
            except OSError as err:
                log.debug("Error reading deployment log %s: %s", target_log, err)

        if not is_active:
            if last_update and not last_update.acknowledged:
                if last_update.status == "failed":
                    default_steps[0].status = "failed"
                    return DeploymentProgressResponse(
                        operation="portal_update",
                        active=False,
                        current_step=1,
                        total_steps=5,
                        step_name=f"Deployment failed: {last_update.summary}",
                        percentage=0,
                        elapsed_seconds=last_update.duration_seconds,
                        estimated_remaining_seconds=0,
                        steps=default_steps,
                        log_tail=log_lines,
                        last_update=last_update,
                    )
                for step in default_steps:
                    step.status = "completed"
                return DeploymentProgressResponse(
                    operation="portal_update",
                    active=False,
                    current_step=5,
                    total_steps=5,
                    step_name="Deployment completed successfully",
                    percentage=100,
                    elapsed_seconds=last_update.duration_seconds,
                    estimated_remaining_seconds=0,
                    steps=default_steps,
                    log_tail=[],
                    last_update=last_update,
                )
            return DeploymentProgressResponse(
                operation="none",
                active=False,
                current_step=0,
                total_steps=5,
                step_name="",
                percentage=0,
                elapsed_seconds=0,
                estimated_remaining_seconds=None,
                steps=default_steps,
                log_tail=log_lines,
                last_update=last_update,
            )

        started_at: float | None = None
        timestamp_fallback: float | None = None
        operation: Literal["portal_update", "tls_certificate", "server_restart", "none"] = "portal_update"
        if self.lock_file.is_file():
            try:
                content = self.lock_file.read_text(encoding="utf-8")
                for line in content.splitlines():
                    if line.startswith("started_at="):
                        started_at = float(line.split("=", 1)[1].strip())
                    elif line.startswith("timestamp="):
                        timestamp_fallback = float(line.split("=", 1)[1].strip())
                    elif line.startswith("operation="):
                        op_val = line.split("=", 1)[1].strip()
                        if op_val in ("portal_update", "tls_certificate", "server_restart", "none"):
                            operation = op_val  # type: ignore[assignment]
            except OSError as err:
                log.debug("Filesystem error reading lock file metadata: %s", err)
            except ValueError as err:
                log.debug("Value error parsing lock file metadata: %s", err)

        effective_start = started_at if started_at is not None else (timestamp_fallback or time.time())
        elapsed_seconds = max(0, int(time.time() - effective_start))

        completed_steps: set[int] = set()
        started_steps: set[int] = set()
        step_pattern = re.compile(r"\[(?:STEP\s+)?(\d+)/(\d+)\]\s+(.*?)(?:\.\.\.|\s*$)")

        for line in log_lines:
            match = step_pattern.search(line)
            if match:
                idx = int(match.group(1))
                started_steps.add(idx)
            if "[ OK ]" in line:
                match_ok = step_pattern.search(line)
                if match_ok:
                    completed_steps.add(int(match_ok.group(1)))
                elif started_steps:
                    completed_steps.add(max(started_steps))

        current_step_idx = 1
        for s in range(1, 6):
            if s in completed_steps:
                default_steps[s - 1].status = "completed"
            elif s in started_steps:
                default_steps[s - 1].status = "running"
                current_step_idx = s
            elif s == 1 and not started_steps:
                default_steps[0].status = "running"
                current_step_idx = 1
            else:
                default_steps[s - 1].status = "pending"

        if completed_steps:
            max_comp = max(completed_steps)
            if max_comp < 5:
                current_step_idx = max_comp + 1
                if current_step_idx <= 5 and default_steps[current_step_idx - 1].status == "pending":
                    default_steps[current_step_idx - 1].status = "running"
            else:
                current_step_idx = 5

        current_step_name = default_steps[current_step_idx - 1].name if 1 <= current_step_idx <= 5 else ""

        comp_count = len(completed_steps)
        if comp_count == 5:
            percent = 100
        else:
            base_pct = comp_count * 20
            partial = 10 if default_steps[current_step_idx - 1].status == "running" else 0
            percent = min(95, max(5, base_pct + partial))

        estimated_remaining: int | None = max(5, 45 - elapsed_seconds)
        has_aborted = any("Deployment aborted with error" in line for line in log_lines)
        if has_aborted:
            if 1 <= current_step_idx <= 5:
                default_steps[current_step_idx - 1].status = "failed"
            return DeploymentProgressResponse(
                operation=operation,
                active=False,
                current_step=current_step_idx,
                total_steps=5,
                step_name=f"Deployment aborted at step {current_step_idx}",
                percentage=percent,
                elapsed_seconds=elapsed_seconds,
                estimated_remaining_seconds=0,
                steps=default_steps,
                log_tail=log_lines,
                last_update=last_update,
            )

        return DeploymentProgressResponse(
            operation=operation,
            active=True,
            current_step=current_step_idx,
            total_steps=5,
            step_name=current_step_name,
            percentage=percent,
            elapsed_seconds=elapsed_seconds,
            estimated_remaining_seconds=estimated_remaining,
            steps=default_steps,
            log_tail=log_lines,
            last_update=last_update,
        )

    async def apply_update(self, branch: str | None = None) -> UpdateApplyResponse:
        """Acquires the deployment lock and dispatches the detached host deployer script.

        Args:
            branch: Optional git branch name override; defaults to configured branch.

        Returns:
            UpdateApplyResponse: Execution metadata acknowledging update invocation.

        Raises:
            HTTPException: 409 Conflict if an update deployment is currently in progress.
            HTTPException: 400 Bad Request if branch name contains invalid characters.
            HTTPException: 500 Internal Server Error if deploy script cannot be located.
        """
        target_branch = branch or self.branch
        if not re.match(r"^[a-zA-Z0-9_.-]+$", target_branch):
            raise HTTPException(status_code=400, detail="Invalid target branch name.")

        async with self._async_lock:
            if self.is_update_in_progress():
                raise HTTPException(
                    status_code=409,
                    detail="An update deployment is already in progress.",
                )

            # Acquire lock file by writing current PID, timestamp, and metadata
            try:
                self.lock_file.parent.mkdir(parents=True, exist_ok=True)
                now_epoch = time.time()
                lock_payload = (
                    f"pid={os.getpid()}\n"
                    f"started_at={now_epoch}\n"
                    f"timestamp={now_epoch}\n"
                    f"branch={target_branch}\n"
                    f"operation=portal_update\n"
                )
                self.lock_file.write_text(lock_payload, encoding="utf-8")
            except OSError as err:
                log.error("Failed creating update lock file %s: %s", self.lock_file, err)
                raise HTTPException(
                    status_code=500,
                    detail="Failed to initialize deployment concurrency lock.",
                ) from err

        deploy_script = self.resolve_deploy_script()
        log.info("Initiating update deployment on branch '%s' via %s", target_branch, deploy_script)

        # Dispatch via injected runner if configured (unit testing / dry-run)
        if self.command_runner is not None:
            try:
                await asyncio.to_thread(self.command_runner, str(deploy_script), target_branch)
            except Exception as err:
                self.lock_file.unlink(missing_ok=True)
                log.error("Command runner failed during update deployment: %s", err)
                raise HTTPException(status_code=500, detail=f"Deployer execution failed: {err}") from err
        else:
            if not deploy_script.is_file():
                self.lock_file.unlink(missing_ok=True)
                log.error("Deploy script not found at %s", deploy_script)
                raise HTTPException(
                    status_code=500,
                    detail=f"Host deploy script not found at {deploy_script}.",
                )

            try:
                await asyncio.to_thread(_spawn_detached_deployer, deploy_script, target_branch)
            except Exception as err:
                self.lock_file.unlink(missing_ok=True)
                log.error("Failed spawning deployer subprocess: %s", err)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to spawn deployment subprocess: {err}",
                ) from err

        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return UpdateApplyResponse(
            status="applying",
            message="Update process initiated. System service will reload momentarily.",
            target_branch=target_branch,
            triggered_at=now_str,
        )

    async def run_loop(self) -> None:
        """Continuous background execution loop running inside application lifespan."""
        log.info(
            "UpdateWatcher background task started (interval: %ss, branch: %s)",
            self.check_interval_seconds,
            self.branch,
        )
        while True:
            try:
                await self.check_for_updates()
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                log.debug("UpdateWatcher loop received cancellation request.")
                break
            except Exception as err:  # pylint: disable=broad-exception-caught
                log.warning("Unexpected error in UpdateWatcher background loop: %s", err)
                try:
                    await asyncio.sleep(self.check_interval_seconds)
                except asyncio.CancelledError:
                    log.debug("UpdateWatcher loop received cancellation request during recovery sleep.")
                    break
