"""Atomic file operations and crash-safe configuration persistence engine.

Provides thread-safe and crash-safe atomic write semantics, isolated staging contexts,
and rotating backup preservation adhering to the 3 AM Debugger standard.
"""

from __future__ import annotations

import datetime
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from app.core.logger import log

# ==============================================================================
# Domain Types & Contracts (Physical Type Isolation)
# ==============================================================================


@dataclass(frozen=True)
class AtomicWriteResult:
    """Immutable telemetry and metadata result for an atomic file write operation.

    Attributes:
        target_path: Resolved filesystem destination path.
        bytes_written: Count of bytes successfully written to storage.
        backup_path: Filesystem path to the created rotating backup snapshot, or None.
        duration_ms: Total elapsed time for staging, syncing, and swapping in milliseconds.
    """

    target_path: Path
    bytes_written: int
    backup_path: Path | None
    duration_ms: float


# ==============================================================================
# Thread Synchronization & Internal Utilities
# ==============================================================================

_ATOMIC_IO_LOCK: threading.Lock = threading.Lock()


def _resolve_target_mode(target: Path, explicit_mode: int | None) -> int:
    """Resolves target file permissions based on explicit override or existing file mode.

    Args:
        target (Path): Destination file path.
        explicit_mode (int | None): Explicit permission mode or None.

    Returns:
        int: Resolved octal permission mode bits.
    """
    if explicit_mode is not None:
        return explicit_mode
    if target.exists():
        try:
            return target.stat().st_mode & 0o777
        except OSError as err:
            log.warning("Failed to query permissions from %s: %s", target, err)
            return 0o644
    return 0o644


def _prune_backups(target: Path, max_backups: int) -> None:
    """Prunes rotating backup files exceeding the max_backups retention threshold.

    Finds all files in `target.parent` matching `{target.name}.bak.*`, sorts them
    by modification timestamp and filename in ascending order, and removes the oldest
    files until the remaining count is at or below `max_backups`.

    Args:
        target (Path): Target file whose backup snapshots should be pruned.
        max_backups (int): Maximum number of backup snapshots to preserve.
    """
    if max_backups < 0:
        return

    pattern = f"{target.name}.bak.*"
    try:
        candidate_backups = list(target.parent.glob(pattern))
    except OSError as err:
        log.warning("Failed to list existing backup files in %s for pattern %s: %s", target.parent, pattern, err)
        return

    def _sort_key(p: Path) -> tuple[float, str]:
        try:
            return (p.stat().st_mtime, p.name)
        except OSError as stat_err:
            log.warning("Failed to inspect modification timestamp on %s: %s", p, stat_err)
            return (0.0, p.name)

    candidate_backups.sort(key=_sort_key)
    excess_count = len(candidate_backups) - max_backups
    if excess_count <= 0:
        return

    for old_backup in candidate_backups[:excess_count]:
        try:
            old_backup.unlink(missing_ok=True)
            log.debug("Pruned excess backup snapshot: %s", old_backup)
        except OSError as err:
            log.warning("Failed to unlink excess backup %s: %s", old_backup, err)


def _create_backup_snapshot(target: Path, max_backups: int) -> Path | None:
    """Creates a timestamped backup snapshot of the target file if it is non-empty.

    Args:
        target (Path): Existing file to snapshot.
        max_backups (int): Maximum number of backup snapshots to preserve.

    Returns:
        Path | None: Path to the created backup snapshot, or None if skipped.
    """
    if not target.exists():
        return None

    try:
        if target.stat().st_size == 0:
            return None
    except OSError as err:
        log.warning("Failed to stat target %s for backup snapshot: %s", target, err)
        return None

    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_path = Path(f"{target}.bak.{timestamp_str}")
    try:
        shutil.copy2(target, backup_path)
    except OSError as err:
        log.warning("Failed to copy %s to backup %s: %s", target, backup_path, err)
        return None

    _prune_backups(target=target, max_backups=max_backups)
    return backup_path


def _fsync_posix_directory(dir_path: Path) -> None:
    """Defensively flushes directory metadata entries to physical media on POSIX systems.

    Ensures that directory entry modifications resulting from file creation or
    atomic replacement are safely written out to non-volatile storage.

    Args:
        dir_path (Path): Path to the directory requiring metadata synchronization.
    """
    try:
        dir_fd = os.open(str(dir_path), os.O_RDONLY)
    except OSError as err:
        log.warning("Failed to open directory %s for fsync: %s", dir_path, err)
        return

    try:
        os.fsync(dir_fd)
    except OSError as err:
        log.warning("Failed to fsync directory descriptor for %s: %s", dir_path, err)
    except AttributeError as err:
        log.warning("Directory fsync unsupported by environment for %s: %s", dir_path, err)
    finally:
        try:
            os.close(dir_fd)
        except OSError as err:
            log.warning("Failed to close directory descriptor for %s: %s", dir_path, err)


# ==============================================================================
# Public Atomic I/O Primitives
# ==============================================================================


# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
# Rationale: Atomic write file orchestrates complete temp-stage-fsync-swap-backup lifecycle
# requiring granular parameters and execution telemetry tracking.
def atomic_write_file(
    target_path: str | Path,
    content: str | bytes,
    max_backups: int = 5,
    make_backup: bool = True,
    mode: int | None = None,
    sync_directory: bool = True,
) -> AtomicWriteResult:
    """Atomically writes content to a target file path using temp-stage-fsync-swap semantics.

    Guarantees thread-safe and crash-safe file persistence. Content is written to a temporary
    file on the same filesystem, flushed to disk, synced via fsync, adjusted for permissions,
    and atomically renamed onto the target file. An optional rotating backup of existing content
    is created prior to the atomic replacement.

    Args:
        target_path (str | Path): Final destination file path.
        content (str | bytes): Text or binary content to write.
        max_backups (int): Maximum number of rotating backup snapshots to retain. Defaults to 5.
        make_backup (bool): If True and target file exists with size > 0, snapshot it. Defaults to True.
        mode (int | None): Octal file permissions (e.g. 0o644). If None, preserves existing mode or defaults to 0o644.
        sync_directory (bool): If True and on POSIX, fsyncs parent directory. Defaults to True.

    Returns:
        AtomicWriteResult: Metadata detailing bytes written, backup path, and duration.

    Raises:
        OSError: If staging, writing, syncing, or replacing fails.
    """
    with _ATOMIC_IO_LOCK:
        start_time = time.perf_counter()
        target = Path(target_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        target_mode = _resolve_target_mode(target, mode)
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
        bytes_written = len(raw_bytes)

        tmp_path: Path | None = None
        replace_completed = False
        backup_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent,
                prefix=".tmp_pal_io_",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(raw_bytes)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            try:
                os.chmod(tmp_path, target_mode)
            except OSError as err:
                log.warning("Failed to set file mode %o on temporary stage %s: %s", target_mode, tmp_path, err)

            if make_backup:
                backup_path = _create_backup_snapshot(target, max_backups)

            os.replace(tmp_path, target)
            replace_completed = True

            if sync_directory and os.name == "posix":
                _fsync_posix_directory(target.parent)

        finally:
            if not replace_completed and tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                    log.debug("Unlinked staging file %s following abortive write.", tmp_path)
                except OSError as err:
                    log.warning("Failed to clean up staging file %s: %s", tmp_path, err)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        return AtomicWriteResult(
            target_path=target,
            bytes_written=bytes_written,
            backup_path=backup_path,
            duration_ms=duration_ms,
        )


def atomic_write_ini(
    target_path: str | Path,
    serialized_content: str,
    max_backups: int = 5,
    make_backup: bool = True,
    sync_directory: bool = True,
) -> Path:
    """Convenience wrapper for atomically persisting serialized INI settings to disk.

    Encodes serialized INI configuration string to UTF-8 and invokes `atomic_write_file`,
    ensuring thread synchronization, backup snapshot creation, and atomic replacement.

    Args:
        target_path (str | Path): Target destination path for the PalWorldSettings.ini file.
        serialized_content (str): Fully formatted INI configuration string.
        max_backups (int): Maximum number of historical backup snapshots to retain. Defaults to 5.
        make_backup (bool): Whether to create a timestamped backup of existing non-empty file. Defaults to True.
        sync_directory (bool): Whether to flush directory metadata descriptor on POSIX. Defaults to True.

    Returns:
        Path: Resolved filesystem path of the written file.
    """
    result = atomic_write_file(
        target_path=target_path,
        content=serialized_content,
        max_backups=max_backups,
        make_backup=make_backup,
        sync_directory=sync_directory,
    )
    return result.target_path


class AtomicStagingContext:
    """Context manager for staging multi-file operations in an isolated directory.

    Allocates a temporary directory in `target_dir.parent` (or inside `target_dir` if
    atomic rename is disabled), yields the staging directory Path, and automatically
    replaces `target_dir` upon successful block completion or cleans up temporary
    artifacts when an exception is raised.

    Attributes:
        target_dir (Path): Resolved destination directory path.
        prefix (str): Prefix used for temporary staging directory names.
        atomic_rename (bool): Whether to replace target_dir on successful exit.
        stage_dir (Path | None): Resolved path of the allocated staging directory.
    """

    def __init__(
        self,
        target_dir: str | Path,
        prefix: str = ".tmp_stage_",
        atomic_rename: bool = True,
    ) -> None:
        """Initializes the staging context with destination directory and naming prefix.

        Args:
            target_dir (str | Path): Destination directory for staged files.
            prefix (str): Prefix applied to temporary directory allocations.
            atomic_rename (bool): If True, replaces target_dir on clean exit.
        """
        self.target_dir: Path = Path(target_dir).resolve()
        self.prefix: str = prefix
        self.atomic_rename: bool = atomic_rename
        self.stage_dir: Path | None = None
        self._committed: bool = False

    def __enter__(self) -> Path:
        """Allocates the temporary staging directory and returns its Path.

        Returns:
            Path: Filesystem path of the newly allocated staging directory.
        """
        if self.atomic_rename:
            self.target_dir.parent.mkdir(parents=True, exist_ok=True)
            parent_dir = self.target_dir.parent
        else:
            if not self.target_dir.is_dir():
                self.target_dir.mkdir(parents=True, exist_ok=True)
            parent_dir = self.target_dir

        staged_str = tempfile.mkdtemp(prefix=self.prefix, dir=parent_dir)
        self.stage_dir = Path(staged_str).resolve()
        self._committed = False
        return self.stage_dir

    def commit(self) -> Path:
        """Atomically commits the staging directory into the target directory.

        Returns:
            Path: Resolved target directory path after commit.

        Raises:
            RuntimeError: If staging directory was not created or was removed.
            OSError: If atomic replacement fails during directory swap.
        """
        if self._committed:
            return self.target_dir

        if self.stage_dir is None or not self.stage_dir.exists():
            raise RuntimeError(f"Cannot commit: staging directory {self.stage_dir} does not exist.")

        if not self.atomic_rename:
            self._committed = True
            return self.stage_dir

        if not self.target_dir.exists():
            os.replace(self.stage_dir, self.target_dir)
            self._committed = True
            return self.target_dir

        timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        tombstone = self.target_dir.with_name(f"{self.target_dir.name}.old.{timestamp_str}")

        os.replace(self.target_dir, tombstone)
        try:
            os.replace(self.stage_dir, self.target_dir)
        except OSError as err:
            log.error("Failed to replace %s with staging %s: %s", self.target_dir, self.stage_dir, err)
            try:
                os.replace(tombstone, self.target_dir)
            except OSError as rollback_err:
                log.critical("Failed to rollback tombstone %s to %s: %s", tombstone, self.target_dir, rollback_err)
            raise

        try:
            shutil.rmtree(tombstone)
        except OSError as err:
            log.warning("Failed to remove temporary directory tombstone %s: %s", tombstone, err)

        self._committed = True
        return self.target_dir

    def cleanup(self) -> None:
        """Removes the temporary staging directory if it has not been committed."""
        if self.stage_dir is not None and self.stage_dir.exists() and not self._committed:
            try:
                shutil.rmtree(self.stage_dir)
                log.debug("Cleaned up uncommitted staging directory: %s", self.stage_dir)
            except OSError as err:
                log.warning("Failed to clean up staging directory %s: %s", self.stage_dir, err)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exits the staging context, committing changes on success or cleaning up on error.

        Args:
            exc_type: Exception class if an exception was raised, otherwise None.
            exc_val: Exception instance if an exception was raised, otherwise None.
            exc_tb: Traceback object if an exception was raised, otherwise None.
        """
        if exc_type is not None:
            self.cleanup()
            return

        if self.atomic_rename and not self._committed:
            self.commit()
        elif not self._committed:
            self.cleanup()


__all__ = [
    "AtomicStagingContext",
    "AtomicWriteResult",
    "atomic_write_file",
    "atomic_write_ini",
]
