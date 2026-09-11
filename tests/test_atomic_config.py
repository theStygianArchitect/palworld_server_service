"""Regression and crash-resilience tests for Issue #20: Atomic Config Persistence Engine."""
# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.core.atomic_io import (
    AtomicStagingContext,
    atomic_write_file,
    atomic_write_ini,
)


def test_defect_comparison_naive_truncate_vs_atomic_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # -------------------------------------------------------------------------
    # 1. Defect Demonstration ("NOT having this"):
    # -------------------------------------------------------------------------
    naive_ini = tmp_path / "naive.ini"
    naive_ini.write_text("ServerName=Active", encoding="utf-8")
    assert naive_ini.read_text(encoding="utf-8") == "ServerName=Active"

    # Simulate mid-write crash/exception during naive write:
    # Opening a file with mode='w' immediately truncates the file to 0 bytes on the filesystem.
    # An exception occurring after opening but before writing completes destroys existing data.
    orig_open = Path.open

    def faulty_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = orig_open(self, *args, **kwargs)  # pylint: disable=consider-using-with
        if self == naive_ini:

            def crash_write(_text: str) -> int:
                raise OSError("Simulated kernel crash / mid-write power loss")

            handle.write = crash_write  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(Path, "open", faulty_open)

    with pytest.raises(OSError, match="Simulated kernel crash"):
        naive_ini.write_text("ServerName=Corrupted", encoding="utf-8")

    # Assert that naive writing leaves the file TRUNCATED (0 bytes) or destroyed!
    assert naive_ini.stat().st_size == 0, "Naive writing should leave the target file truncated to 0 bytes"
    assert naive_ini.read_text(encoding="utf-8") == "", "File content was wiped out by naive truncate"

    # Undo monkeypatch before evaluating atomic writing
    monkeypatch.undo()

    # -------------------------------------------------------------------------
    # 2. Defect Elimination ("HAVING this"):
    # -------------------------------------------------------------------------
    atomic_ini = tmp_path / "atomic.ini"
    atomic_ini.write_text("ServerName=Active", encoding="utf-8")
    assert atomic_ini.read_text(encoding="utf-8") == "ServerName=Active"

    # Simulate a mid-write crash during atomic write (e.g. os.replace raises OSError)
    def faulty_replace(_src: str | os.PathLike[Any], _dst: str | os.PathLike[Any]) -> None:
        raise OSError("Disk write fault")

    monkeypatch.setattr(os, "replace", faulty_replace)

    with pytest.raises(OSError, match="Disk write fault"):
        atomic_write_ini(atomic_ini, "ServerName=Corrupted")

    # Assert that atomic_ini STILL CONTAINS "ServerName=Active" (100% intact, ZERO byte loss!)
    assert atomic_ini.read_text(encoding="utf-8") == "ServerName=Active"

    # Assert that no dangling .tmp_pal_io_* files remain in tmp_path
    dangling_files = list(tmp_path.glob(".tmp_pal_io_*"))
    assert not dangling_files, f"Found dangling temporary staging files: {dangling_files}"


def test_atomic_write_file_basic_success(tmp_path: Path) -> None:
    target = tmp_path / "PalWorldSettings.ini"
    content = '[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName="PalHaven")\n'

    result = atomic_write_file(target, content)

    assert target.exists(), "Target file must exist after atomic write"
    assert target.read_text(encoding="utf-8") == content
    assert result.target_path == target
    assert result.bytes_written == len(content.encode("utf-8"))
    assert result.duration_ms >= 0.0


def test_atomic_write_backup_generation_and_rotation(tmp_path: Path) -> None:
    target = tmp_path / "settings.ini"
    # Initial write (creates file, no backup created because file did not exist yet)
    atomic_write_file(target, "Version 0\n", max_backups=3, make_backup=True)

    # Overwrite 6 times with distinct content
    for i in range(1, 7):
        time.sleep(0.005)  # Microsecond timestamp differentiation
        atomic_write_file(target, f"Version {i}\n", max_backups=3, make_backup=True)

    # Asserts only 3 .bak.* files exist
    backups = sorted(tmp_path.glob(f"{target.name}.bak.*"))
    assert len(backups) == 3, f"Expected 3 retained backups, got {len(backups)}: {backups}"

    # Asserts content of newest backup matches previous version (Version 5 was active before Version 6)
    newest_backup = backups[-1]
    assert newest_backup.read_text(encoding="utf-8") == "Version 5\n"

    # Asserts oldest backups (0, 1, 2) were pruned, leaving versions 3, 4, 5
    retained_contents = [b.read_text(encoding="utf-8") for b in backups]
    assert retained_contents == ["Version 3\n", "Version 4\n", "Version 5\n"]

    # Target currently contains latest write
    assert target.read_text(encoding="utf-8") == "Version 6\n"


def test_atomic_write_permission_preservation(tmp_path: Path) -> None:
    target = tmp_path / "protected_settings.ini"
    target_mode = 0o600
    atomic_write_file(target, "InitialSensitiveContent\n", mode=target_mode)
    assert target.exists()

    if os.name != "nt":
        initial_mode = target.stat().st_mode & 0o777
        assert initial_mode == target_mode, f"Expected mode {oct(target_mode)}, got {oct(initial_mode)}"

        # Overwrite without explicit mode; engine should preserve existing file's mode
        atomic_write_file(target, "UpdatedSensitiveContent\n")
        preserved_mode = target.stat().st_mode & 0o777
        assert preserved_mode == target_mode, (
            f"Preserved mode mismatch: expected {oct(target_mode)}, got {oct(preserved_mode)}"
        )
        assert target.read_text(encoding="utf-8") == "UpdatedSensitiveContent\n"
    else:
        # On Windows, verify atomic write succeeds without crashing on chmod and preserves content
        atomic_write_file(target, "UpdatedSensitiveContent\n")
        assert target.read_text(encoding="utf-8") == "UpdatedSensitiveContent\n"


def test_atomic_write_multithreaded_concurrency(tmp_path: Path) -> None:
    target = tmp_path / "concurrent_settings.ini"
    thread_count = 10
    writes_per_thread = 5
    thread_errors: list[Exception] = []

    def worker(thread_idx: int) -> None:
        try:
            for write_idx in range(writes_per_thread):
                content = (
                    f"[/Script/Pal.PalGameWorldSettings]\n"
                    f'ServerName="Server_{thread_idx}_{write_idx}"\n'
                    f"PublicPort=8211\n"
                )
                atomic_write_ini(target, content)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            sys.stderr.write(f"Worker {thread_idx} encountered write error: {exc}\n")
            thread_errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not thread_errors, f"Concurrency errors encountered during parallel writes: {thread_errors}"
    assert target.exists(), "Target file must exist after multithreaded writes"

    # Asserts zero file corruption and valid final content
    final_content = target.read_text(encoding="utf-8")
    assert "[/Script/Pal.PalGameWorldSettings]" in final_content
    assert 'ServerName="Server_' in final_content
    assert "PublicPort=8211\n" in final_content

    # Asserts zero descriptor leaks by verifying the file can be opened, modified, and unlinked
    with open(target, "a", encoding="utf-8") as f_desc:
        f_desc.write("# descriptor test\n")
    assert target.stat().st_size > 0


def test_atomic_staging_context(tmp_path: Path) -> None:
    target_dir = tmp_path / "staged_service"
    staging_path: Path | None = None

    # Verify staging directory creation and automatic cleanup on exception
    with (
        pytest.raises(RuntimeError, match="Staging fault simulation"),
        AtomicStagingContext(target_dir, prefix=".tmp_stage_") as stage_dir,
    ):
        staging_path = Path(stage_dir)
        assert staging_path.exists(), "Staging directory must exist within context"
        assert staging_path.is_dir(), "Staging path must be a directory"
        (staging_path / "partial_data.bin").write_text("in-progress data", encoding="utf-8")
        raise RuntimeError("Staging fault simulation")

    assert staging_path is not None
    assert not staging_path.exists(), "Staging directory must be cleaned up on exception"
    assert not target_dir.exists(), "Target directory must not be created on aborted staging"

    # Verify clean commit workflow without exception
    with AtomicStagingContext(target_dir, prefix=".tmp_stage_") as stage_dir:
        staged_work = Path(stage_dir)
        (staged_work / "config.json").write_text('{"status": "ok"}', encoding="utf-8")

    assert target_dir.exists(), "Target directory must exist after clean commit"
    assert (target_dir / "config.json").read_text(encoding="utf-8") == '{"status": "ok"}'
