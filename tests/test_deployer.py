"""Unit tests for release deployment primitives and atomic staging engine."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import os
import tarfile
from pathlib import Path
from typing import Any

import pytest

from app.engine.deployer import (
    DEFAULT_DEPLOY_PREFIX,
    SHA256_BLOCK_SIZE,
    DeploymentPlan,
    DeploymentResult,
    DeployStage,
    SecurityError,
    compute_sha256,
    execute_atomic_swap,
    stage_release_package,
    verify_checksum,
)


def _create_mock_tarball(tar_path: Path, files: dict[str, bytes]) -> Path:
    """Helper to create a tar.gz archive with specified relative files.

    Args:
        tar_path (Path): Path to output tar.gz file.
        files (dict[str, bytes]): Mapping of member paths to file contents.

    Returns:
        Path: Created archive path.
    """
    with tarfile.open(tar_path, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return tar_path


def test_compute_sha256_and_verify(tmp_path: Path) -> None:
    """Verifies SHA256 computation and constant-time checksum verification."""
    # 1. Test small file
    small_payload = b"Palworld Server Deployment Test Data\n"
    small_file = tmp_path / "small.bin"
    small_file.write_bytes(small_payload)

    expected_small_hash = hashlib.sha256(small_payload).hexdigest()
    assert compute_sha256(small_file) == expected_small_hash
    assert verify_checksum(small_file, expected_small_hash) is True
    assert verify_checksum(small_file, "0" * 64) is False
    assert verify_checksum(small_file, expected_small_hash.upper()) is True

    # 2. Test large file exceeding 64KB block size to exercise streaming loop
    large_payload = b"BlockStreamTestPayload1234567890!\n" * 5000  # ~170 KB
    assert len(large_payload) > SHA256_BLOCK_SIZE * 2

    large_file = tmp_path / "large.bin"
    large_file.write_bytes(large_payload)

    expected_large_hash = hashlib.sha256(large_payload).hexdigest()
    assert compute_sha256(large_file) == expected_large_hash
    assert verify_checksum(large_file, expected_large_hash) is True

    # 3. Test non-existent file
    missing_file = tmp_path / "non_existent.bin"
    with pytest.raises(FileNotFoundError):
        compute_sha256(missing_file)
    assert verify_checksum(missing_file, expected_small_hash) is False

    # 4. Test directory target
    sub_dir = tmp_path / "test_dir"
    sub_dir.mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        compute_sha256(sub_dir)
    assert verify_checksum(sub_dir, expected_small_hash) is False


def test_stage_release_package_safe_extraction(tmp_path: Path) -> None:
    """Verifies that a valid release package extracts cleanly via AtomicStagingContext."""
    tarball_path = tmp_path / "release_v0.3.0.tar.gz"
    package_contents = {
        "manifest.json": b'{"version": "0.3.0", "app": "palmanager"}',
        "app/main.py": b"print('Palworld Service Active')\n",
        "config/settings.ini": b"[Server]\nPort=8211\n",
    }
    _create_mock_tarball(tarball_path, package_contents)

    target_dir = tmp_path / "staged_app"
    result_path = stage_release_package(tarball_path, target_dir)

    assert result_path == target_dir.resolve()
    assert target_dir.is_dir()
    assert (target_dir / "manifest.json").read_text(encoding="utf-8") == '{"version": "0.3.0", "app": "palmanager"}'
    assert (target_dir / "app" / "main.py").read_text(encoding="utf-8") == "print('Palworld Service Active')\n"
    assert (target_dir / "config" / "settings.ini").read_text(encoding="utf-8") == "[Server]\nPort=8211\n"

    # Verify temporary deployment prefix directories were cleaned up
    lingering_tmp = list(tmp_path.glob(f"{DEFAULT_DEPLOY_PREFIX}*"))
    assert not lingering_tmp, f"Temporary directories remained uncleaned: {lingering_tmp}"

    # Missing archive raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        stage_release_package(tmp_path / "missing.tar.gz", tmp_path / "out")

    # Corrupted archive raises ValueError
    corrupt_tar = tmp_path / "corrupt.tar.gz"
    corrupt_tar.write_bytes(b"NOT_A_TARBALL_DATA")
    with pytest.raises(ValueError, match="Invalid tarball archive"):
        stage_release_package(corrupt_tar, tmp_path / "out_corrupt")


def test_stage_release_package_path_traversal_prevention(tmp_path: Path) -> None:
    """Verifies path traversal attempts in archives are rejected and no files escape."""
    # 1. Malicious tarball with relative directory traversal member ('../evil.txt')
    malicious_tar = tmp_path / "malicious_traversal.tar.gz"
    _create_mock_tarball(
        malicious_tar,
        {
            "../evil.txt": b"ATTACKER_DATA_OUTSIDE_ROOT",
            "normal.txt": b"NORMAL_CONTENT",
        },
    )

    staged_dest = tmp_path / "staged_malicious"
    with pytest.raises((ValueError, SecurityError), match=r"Directory traversal|Security violation"):
        stage_release_package(malicious_tar, staged_dest)

    # Ensure evil file was NOT written outside staging root
    assert not (tmp_path / "evil.txt").exists(), "Traversed file must not be written to filesystem"
    # Ensure staging destination was rolled back and does not exist
    assert not staged_dest.exists(), "Target directory must not be created on aborted staging"

    # 2. Malicious tarball with nested traversal ('dir/../../escape.txt')
    nested_tar = tmp_path / "malicious_nested.tar.gz"
    _create_mock_tarball(
        nested_tar,
        {
            "sub/../../escape.txt": b"ESCAPE_DATA",
        },
    )

    nested_dest = tmp_path / "staged_nested"
    with pytest.raises((ValueError, SecurityError)):
        stage_release_package(nested_tar, nested_dest)

    assert not (tmp_path / "escape.txt").exists()
    assert not nested_dest.exists()

    # 3. Malicious tarball with absolute path member ('/etc/malicious.txt')
    abs_tar = tmp_path / "malicious_abs.tar.gz"
    with tarfile.open(abs_tar, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="/etc/malicious.txt")
        info.size = 11
        archive.addfile(info, io.BytesIO(b"ABSOLUTE_PW"))

    abs_dest = tmp_path / "staged_abs"
    with pytest.raises((ValueError, SecurityError)):
        stage_release_package(abs_tar, abs_dest)

    assert not abs_dest.exists()


def test_stage_release_package_symlink_traversal_prevention(tmp_path: Path) -> None:
    """Verifies malicious symlink targets attempting escape are rejected."""
    symlink_tar = tmp_path / "symlink_attack.tar.gz"
    with tarfile.open(symlink_tar, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="symlink_escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../escaped_file"
        archive.addfile(info)

    symlink_dest = tmp_path / "staged_symlink"
    with pytest.raises((ValueError, SecurityError)):
        stage_release_package(symlink_tar, symlink_dest)

    assert not symlink_dest.exists()


def test_execute_atomic_swap(tmp_path: Path) -> None:
    """Verifies atomic directory replacement and backup archiving."""
    # 1. Clean swap with existing target and backup archiving
    target_dir = tmp_path / "live_app"
    target_dir.mkdir()
    (target_dir / "version.txt").write_text("v1.0.0", encoding="utf-8")
    (target_dir / "old_only.txt").write_text("old data", encoding="utf-8")

    staged_dir = tmp_path / "staged_app"
    staged_dir.mkdir()
    (staged_dir / "version.txt").write_text("v2.0.0", encoding="utf-8")
    (staged_dir / "new_only.txt").write_text("new data", encoding="utf-8")

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    swap_result = execute_atomic_swap(staged_dir, target_dir, backup_dir=backup_dir)
    assert swap_result is True
    assert (target_dir / "version.txt").read_text(encoding="utf-8") == "v2.0.0"
    assert (target_dir / "new_only.txt").exists()
    assert not (target_dir / "old_only.txt").exists()
    assert not staged_dir.exists(), "Staged directory should have been swapped into target"

    # Verify backup exists
    backups = list(backup_dir.glob("live_app.backup.*"))
    assert len(backups) == 1
    assert (backups[0] / "version.txt").read_text(encoding="utf-8") == "v1.0.0"
    assert (backups[0] / "old_only.txt").exists()

    # 2. Swap into non-existent target (initial install without backup)
    init_staged = tmp_path / "init_staged"
    init_staged.mkdir()
    (init_staged / "init.txt").write_text("initialization", encoding="utf-8")

    init_target = tmp_path / "init_live"
    assert not init_target.exists()

    assert execute_atomic_swap(init_staged, init_target) is True
    assert init_target.is_dir()
    assert (init_target / "init.txt").read_text(encoding="utf-8") == "initialization"
    assert not init_staged.exists()


def test_execute_atomic_swap_rollback_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that directory swap failure safely rolls back the live target directory."""
    rollback_target = tmp_path / "rb_target"
    rollback_target.mkdir()
    (rollback_target / "state.txt").write_text("original", encoding="utf-8")

    rollback_staged = tmp_path / "rb_staged"
    rollback_staged.mkdir()
    (rollback_staged / "state.txt").write_text("updated", encoding="utf-8")

    orig_replace = os.replace
    call_count = 0

    def faulty_replace(src: str | os.PathLike[Any], dst: str | os.PathLike[Any]) -> None:
        nonlocal call_count
        call_count += 1
        # First call is target -> tombstone (allow it)
        # Second call is staged -> target (raise error to simulate crash/fault)
        if call_count == 2:
            raise OSError("Simulated filesystem locked during directory swap")
        orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", faulty_replace)
    failed_result = execute_atomic_swap(rollback_staged, rollback_target)
    assert failed_result is False

    # Target must have rolled back to its original state!
    assert rollback_target.exists()
    assert (rollback_target / "state.txt").read_text(encoding="utf-8") == "original"


def test_execute_atomic_swap_invalid_inputs(tmp_path: Path) -> None:
    """Verifies that invalid source or matching paths are rejected safely."""
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    assert execute_atomic_swap(tmp_path / "nonexistent", live_dir) is False
    assert execute_atomic_swap(live_dir, live_dir) is False


def test_deployment_plan_and_result_dataclasses() -> None:
    """Verifies schema invariants, immutability, and typing for Deployment dataclasses."""
    tarball = Path("/tmp/palworld-v0.3.0.tar.gz")
    app_dir = Path("/opt/palmanager")
    backup_dir = Path("/opt/palmanager/backups")

    plan = DeploymentPlan(
        version="0.3.0",
        release_tarball=tarball,
        target_app_dir=app_dir,
        backup_dir=backup_dir,
    )

    assert plan.version == "0.3.0"
    assert plan.release_tarball == tarball
    assert plan.target_app_dir == app_dir
    assert plan.backup_dir == backup_dir
    assert dataclasses.is_dataclass(plan)

    # Immutability check
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.version = "0.4.0"  # type: ignore[misc]

    # String path coercion check
    plan_str = DeploymentPlan(
        version="0.3.1",
        release_tarball="/tmp/pkg.tar.gz",  # type: ignore[arg-type]
        target_app_dir="/var/www",  # type: ignore[arg-type]
        backup_dir="/var/backups",  # type: ignore[arg-type]
    )
    assert isinstance(plan_str.release_tarball, Path)
    assert isinstance(plan_str.target_app_dir, Path)
    assert isinstance(plan_str.backup_dir, Path)

    # DeploymentResult validation
    result = DeploymentResult(
        success=True,
        stage=DeployStage.COMPLETE,
        message="Deployment completed successfully",
        active_version="0.3.0",
        duration_ms=123.45,
    )

    assert result.success is True
    assert result.stage == DeployStage.COMPLETE
    assert result.stage.value == "complete"
    assert result.message == "Deployment completed successfully"
    assert result.active_version == "0.3.0"
    assert result.duration_ms == 123.45
    assert dataclasses.is_dataclass(result)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False  # type: ignore[misc]

    # DeployStage Enum completeness
    assert DeployStage.DOWNLOAD == "download"
    assert DeployStage.VERIFY == "verify"
    assert DeployStage.STAGE == "stage"
    assert DeployStage.SWAP == "swap"
    assert DeployStage.ROLLBACK == "rollback"
    assert DeployStage.COMPLETE == "complete"

    # SecurityError hierarchy
    assert issubclass(SecurityError, ValueError)
