"""Deployment primitives and atomic release staging engine.

Provides release package checksum verification, isolated atomic extraction with path
traversal defense, and crash-safe directory swapping conforming to the 3 AM Debugger standard.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import shutil
import tarfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.atomic_io import AtomicStagingContext
from app.core.logger import log

SHA256_BLOCK_SIZE: int = 65536
DEFAULT_DEPLOY_PREFIX: str = ".tmp_deploy_"

# ==============================================================================
# Domain Types & Contracts (Physical Type Isolation)
# ==============================================================================


class SecurityError(ValueError):
    """Raised when an untrusted archive or path traversal attack is detected."""


class DeployStage(str, Enum):
    """Lifecycle stages for release deployment execution."""

    DOWNLOAD = "download"
    VERIFY = "verify"
    STAGE = "stage"
    SWAP = "swap"
    ROLLBACK = "rollback"
    COMPLETE = "complete"


@dataclass(frozen=True)
class DeploymentPlan:
    """Immutable configuration plan for executing a release deployment.

    Attributes:
        version (str): Semantic version identifier being deployed.
        release_tarball (Path): Local filesystem path to release archive.
        target_app_dir (Path): Destination installation directory.
        backup_dir (Path | None): Directory to archive previous installation, if any.
    """

    version: str
    release_tarball: Path
    target_app_dir: Path
    backup_dir: Path | None = None

    def __post_init__(self) -> None:
        """Coerces path-like arguments into resolved Path instances."""
        if isinstance(self.release_tarball, str):
            object.__setattr__(self, "release_tarball", Path(self.release_tarball))
        if isinstance(self.target_app_dir, str):
            object.__setattr__(self, "target_app_dir", Path(self.target_app_dir))
        if isinstance(self.backup_dir, str):
            object.__setattr__(self, "backup_dir", Path(self.backup_dir))


@dataclass(frozen=True)
class DeploymentResult:
    """Immutable telemetry and outcome record of a deployment attempt.

    Attributes:
        success (bool): True if deployment completed without failure, False otherwise.
        stage (DeployStage): Last lifecycle stage reached or executed.
        message (str): Diagnostic outcome or failure rationale.
        active_version (str): Currently active semantic version after execution.
        duration_ms (float): Total elapsed deployment execution time in milliseconds.
    """

    success: bool
    stage: DeployStage
    message: str
    active_version: str
    duration_ms: float


# ==============================================================================
# Security & Path Traversal Guards
# ==============================================================================


def _validate_tar_member(member: tarfile.TarInfo, destination_root: Path) -> None:
    """Validates that a tarball member does not attempt directory traversal attacks.

    Args:
        member (tarfile.TarInfo): Archive member metadata.
        destination_root (Path): Root directory where archive will be extracted.

    Raises:
        SecurityError: If the member path traverses outside destination_root or uses absolute paths.
    """
    member_path = Path(member.name)

    # 1. Guard against absolute paths and drive letters (e.g. /etc/passwd or C:\Windows)
    if member_path.is_absolute() or member.name.startswith(("/", "\\")) or ":" in member.name:
        log.error("Security violation: absolute or drive-rooted tar member path: %s", member.name)
        raise SecurityError(f"Absolute or drive-rooted tar member path rejected: {member.name}")

    # 2. Guard against explicit parent directory traversal components ('..')
    if ".." in member_path.parts:
        log.error("Security violation: directory traversal '..' detected in member: %s", member.name)
        raise SecurityError(f"Directory traversal component '..' rejected: {member.name}")

    # 3. Guard against resolved destination path escape
    try:
        resolved_root = destination_root.resolve()
        resolved_target = (destination_root / member_path).resolve()
    except RuntimeError as err:
        log.error("Security violation: path resolution runtime error for %s: %s", member.name, err)
        raise SecurityError(f"Path resolution error for member {member.name}: {err}") from err
    except OSError as err:
        log.error("Security violation: path resolution OS error for %s: %s", member.name, err)
        raise SecurityError(f"Path resolution OS error for member {member.name}: {err}") from err

    if not resolved_target.is_relative_to(resolved_root):
        log.error("Security violation: resolved path escapes destination root: %s", resolved_target)
        raise SecurityError(f"Resolved member path escapes extraction root: {member.name}")

    # 4. Guard against malicious symlinks / hardlinks escaping destination root
    if member.issym() or member.islnk():
        link_target_str = member.linkname
        if link_target_str.startswith(("/", "\\")) or ":" in link_target_str:
            log.error("Security violation: absolute link target in member %s: %s", member.name, link_target_str)
            raise SecurityError(f"Absolute link target rejected in member: {member.name}")

        link_path = Path(link_target_str)
        if ".." in link_path.parts:
            log.error("Security violation: link traversal '..' detected in member %s: %s", member.name, link_target_str)
            raise SecurityError(f"Link traversal '..' rejected in member: {member.name}")

        try:
            resolved_link = (resolved_target.parent / link_path).resolve()
        except RuntimeError as err:
            log.error("Security violation: link resolution runtime error for %s: %s", member.name, err)
            raise SecurityError(f"Link resolution error for member {member.name}: {err}") from err
        except OSError as err:
            log.error("Security violation: link resolution OS error for %s: %s", member.name, err)
            raise SecurityError(f"Link resolution OS error for member {member.name}: {err}") from err

        if not resolved_link.is_relative_to(resolved_root):
            log.error("Security violation: link escapes extraction root: %s -> %s", member.name, link_target_str)
            raise SecurityError(f"Symlink escapes extraction root in member: {member.name}")


# ==============================================================================
# Hashing & Integrity Operations
# ==============================================================================


def compute_sha256(file_path: Path) -> str:
    """Computes the SHA256 hex digest of a file by streaming in 64KB chunks.

    Args:
        file_path (Path): Path to the target file.

    Returns:
        str: Hexadecimal representation of the SHA256 digest.

    Raises:
        FileNotFoundError: If target file does not exist.
        ValueError: If the target path points to a directory.
        OSError: If reading target file fails.
    """
    resolved_path = file_path.resolve()
    if not resolved_path.exists():
        log.error("File not found for SHA256 computation: %s", resolved_path)
        raise FileNotFoundError(f"File not found for SHA256 computation: {resolved_path}")

    if not resolved_path.is_file():
        log.error("Target path is not a regular file for SHA256 computation: %s", resolved_path)
        raise ValueError(f"Target path is not a regular file: {resolved_path}")

    hasher = hashlib.sha256()
    try:
        with resolved_path.open("rb") as stream:
            while True:
                chunk = stream.read(SHA256_BLOCK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError as err:
        log.error("I/O error streaming file %s for SHA256: %s", resolved_path, err)
        raise

    return hasher.hexdigest()


def verify_checksum(file_path: Path, expected_sha256: str) -> bool:
    """Verifies file SHA256 checksum against an expected hash using constant-time comparison.

    Args:
        file_path (Path): Target file to verify.
        expected_sha256 (str): Expected SHA256 hexadecimal digest string.

    Returns:
        bool: True if file hash matches expected digest, False otherwise.
    """
    resolved_path = file_path.resolve()
    if not resolved_path.is_file():
        log.warning("Checksum verification failed: file does not exist or is not a file: %s", resolved_path)
        return False

    try:
        calculated_sha256 = compute_sha256(resolved_path)
    except FileNotFoundError as err:
        log.warning("File not found during checksum verification for %s: %s", resolved_path, err)
        return False
    except ValueError as err:
        log.warning("Invalid target for checksum computation %s: %s", resolved_path, err)
        return False
    except OSError as err:
        log.error("I/O error computing checksum for %s: %s", resolved_path, err)
        return False

    is_valid = hmac.compare_digest(calculated_sha256.lower().strip(), expected_sha256.lower().strip())
    if not is_valid:
        log.warning(
            "Checksum mismatch for %s: computed %s, expected %s",
            resolved_path,
            calculated_sha256,
            expected_sha256,
        )
    return is_valid


# ==============================================================================
# Release Staging & Atomic Swapping Operations
# ==============================================================================


def stage_release_package(tarball_path: Path, target_dir: Path) -> Path:
    """Safely extracts a release tarball into an isolated staging directory.

    Enforces path traversal security, verifying all tar members before extraction,
    and commits the staging directory atomically into target_dir via AtomicStagingContext.

    Args:
        tarball_path (Path): Filesystem path to release tarball archive.
        target_dir (Path): Destination directory for staged extraction.

    Returns:
        Path: Resolved path to the verified staged directory.

    Raises:
        FileNotFoundError: If tarball archive does not exist.
        ValueError: If archive is invalid, corrupted, or not a file.
        SecurityError: If archive contains members attempting directory traversal.
        RuntimeError: If staging context fails to commit.
        OSError: If I/O operations fail during extraction.
    """
    resolved_tarball = tarball_path.resolve()
    resolved_target = target_dir.resolve()

    if not resolved_tarball.exists():
        log.error("Release tarball not found: %s", resolved_tarball)
        raise FileNotFoundError(f"Release tarball not found: {resolved_tarball}")

    if not resolved_tarball.is_file():
        log.error("Release tarball is not a regular file: %s", resolved_tarball)
        raise ValueError(f"Release tarball is not a regular file: {resolved_tarball}")

    with AtomicStagingContext(resolved_target, prefix=DEFAULT_DEPLOY_PREFIX) as stage_path:
        try:
            with tarfile.open(resolved_tarball, mode="r:*") as archive:
                members = archive.getmembers()
                for member in members:
                    _validate_tar_member(member, stage_path)

                if hasattr(tarfile, "data_filter"):
                    archive.extractall(path=stage_path, filter="data")
                else:
                    archive.extractall(path=stage_path, members=members)  # nosec B202 - members pre-validated
        except tarfile.ReadError as err:
            log.error("Failed to read release tarball %s: %s", resolved_tarball, err)
            raise ValueError(f"Invalid tarball archive: {resolved_tarball}") from err
        except tarfile.CompressionError as err:
            log.error("Compression error reading release tarball %s: %s", resolved_tarball, err)
            raise ValueError(f"Compression error in tarball: {resolved_tarball}") from err
        except SecurityError as err:
            log.error("Security violation during tarball staging: %s", err)
            raise
        except OSError as err:
            log.error("I/O error during tarball staging extraction for %s: %s", resolved_tarball, err)
            raise

    return resolved_target


def _perform_initial_target_move(staged_dir: Path, target_dir: Path) -> bool:
    """Moves staged directory directly into a non-existent target destination.

    Args:
        staged_dir (Path): Source directory containing staged files.
        target_dir (Path): Destination directory to initialize.

    Returns:
        bool: True if move succeeded, False otherwise.
    """
    try:
        os.replace(staged_dir, target_dir)
        log.info("Directly moved staged directory %s to initial target %s", staged_dir, target_dir)
        return True
    except OSError as err:
        log.warning(
            "Atomic rename failed (%s), attempting fallback directory move from %s to %s",
            err,
            staged_dir,
            target_dir,
        )
        try:
            shutil.move(str(staged_dir), str(target_dir))
            return True
        except OSError as move_err:
            log.error("Failed to move staged directory %s to target %s: %s", staged_dir, target_dir, move_err)
            return False


def _swap_directories_with_rollback(staged_dir: Path, target_dir: Path, tombstone: Path) -> bool:
    """Swaps staged directory into target directory using a temporary tombstone with rollback safety.

    Args:
        staged_dir (Path): Path to directory being swapped in.
        target_dir (Path): Path to current target directory.
        tombstone (Path): Temporary location holding displaced target directory.

    Returns:
        bool: True if swap succeeded, False if swap failed and was rolled back.
    """
    try:
        os.replace(target_dir, tombstone)
    except OSError as err:
        log.error("Failed to move target directory %s to tombstone %s: %s", target_dir, tombstone, err)
        return False

    try:
        os.replace(staged_dir, target_dir)
        return True
    except OSError as swap_err:
        log.error("Failed to swap staged %s into target %s: %s", staged_dir, target_dir, swap_err)
        try:
            os.replace(tombstone, target_dir)
            log.info("Successfully rolled back tombstone %s to target %s", tombstone, target_dir)
        except OSError as rollback_err:
            log.critical("Failed to rollback tombstone %s to target %s: %s", tombstone, target_dir, rollback_err)
        return False


def _archive_or_cleanup_tombstone(
    tombstone: Path,
    target_name: str,
    backup_dir: Path | None,
    timestamp_str: str,
) -> None:
    """Archives replaced directory tombstone to backup directory or cleans it up.

    Args:
        tombstone (Path): Path to replaced tombstone directory.
        target_name (str): Original name of target directory.
        backup_dir (Path | None): Optional directory destination for backup archives.
        timestamp_str (str): UTC timestamp string associated with the swap.
    """
    if backup_dir is None:
        try:
            shutil.rmtree(tombstone)
        except OSError as cleanup_err:
            log.warning("Failed to clean up temporary directory tombstone %s: %s", tombstone, cleanup_err)
        return

    resolved_backup = backup_dir.resolve()
    try:
        resolved_backup.parent.mkdir(parents=True, exist_ok=True)
        if resolved_backup.is_dir():
            archive_dest = resolved_backup / f"{target_name}.backup.{timestamp_str}"
        else:
            archive_dest = resolved_backup

        try:
            os.replace(tombstone, archive_dest)
        except OSError as rename_err:
            log.debug("Rename to backup %s failed (%s), attempting fallback move", archive_dest, rename_err)
            shutil.move(str(tombstone), str(archive_dest))
        log.info("Archived previous deployment backup to: %s", archive_dest)
    except OSError as backup_err:
        log.warning("Failed to archive backup to %s: %s", resolved_backup, backup_err)
        try:
            shutil.rmtree(tombstone)
        except OSError as cleanup_err:
            log.warning("Failed to remove tombstone %s after backup failure: %s", tombstone, cleanup_err)


def execute_atomic_swap(staged_dir: Path, target_dir: Path, backup_dir: Path | None = None) -> bool:
    """Performs an atomic directory swap or safe backup-and-replace of the target directory.

    If target_dir already exists, it is renamed to a temporary tombstone on the same filesystem,
    staged_dir is moved to target_dir, and if successful, the previous target is archived to backup_dir
    or removed. If swapping staged_dir into target_dir fails, the previous target is restored.

    Args:
        staged_dir (Path): Staged directory containing extracted new release.
        target_dir (Path): Destination live application directory.
        backup_dir (Path | None): Optional directory path for archiving previous installation.

    Returns:
        bool: True if swap succeeded and target_dir is live, False otherwise.
    """
    resolved_staged = staged_dir.resolve()
    resolved_target = target_dir.resolve()

    if not resolved_staged.exists() or not resolved_staged.is_dir():
        log.error("Staged directory does not exist or is not a directory: %s", resolved_staged)
        return False

    if resolved_staged == resolved_target:
        log.error("Staged directory cannot be identical to target directory: %s", resolved_staged)
        return False

    try:
        resolved_target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        log.error("Failed to create parent directory for target %s: %s", resolved_target, err)
        return False

    if not resolved_target.exists():
        return _perform_initial_target_move(resolved_staged, resolved_target)

    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    tombstone = resolved_target.with_name(f"{resolved_target.name}.old.{timestamp_str}")

    swap_succeeded = _swap_directories_with_rollback(resolved_staged, resolved_target, tombstone)
    if not swap_succeeded:
        return False

    _archive_or_cleanup_tombstone(tombstone, resolved_target.name, backup_dir, timestamp_str)
    return True


__all__ = [
    "DEFAULT_DEPLOY_PREFIX",
    "SHA256_BLOCK_SIZE",
    "DeployStage",
    "DeploymentPlan",
    "DeploymentResult",
    "SecurityError",
    "compute_sha256",
    "execute_atomic_swap",
    "stage_release_package",
    "verify_checksum",
]
