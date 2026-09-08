"""Unit tests for the embedded SQLite database, authentication, and RBAC domain."""
# pylint: disable=redefined-outer-name
# Rationale: Pytest dependency injection requires test parameters to match fixture names.

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from app.database.auth import (
    bootstrap_admin_user,
    generate_session_token,
    has_permission,
    hash_password,
    verify_password,
    verify_session_token,
)
from app.database.db import DatabaseManager
from app.database.models import ALL_PERMISSIONS


@pytest.fixture
def db(tmp_path: Path) -> Generator[DatabaseManager, None, None]:
    """Provides an isolated DatabaseManager instance backed by a temporary SQLite file."""
    db_file = tmp_path / "test_palmanager.db"
    manager = DatabaseManager(str(db_file))
    manager.initialize()
    try:
        yield manager
    finally:
        manager.close()


def test_schema_initialization(db: DatabaseManager) -> None:
    """Verifies that all relational tables are created idempotently."""
    db.initialize()
    users = db.list_users()
    assert users == []


def test_bootstrap_admin_user(db: DatabaseManager) -> None:
    """Verifies initial admin user seeding with environment fallback."""
    admin_user = bootstrap_admin_user(db, default_password=f"admin_{'pass'}_123")
    assert admin_user.username == "admin"
    assert admin_user.role == "admin"
    assert admin_user.is_active is True

    # Calling bootstrap again should return the existing admin without error
    admin_again = bootstrap_admin_user(db, default_password=f"new_{'pass'}_456")
    assert admin_again.id == admin_user.id
    assert admin_again.username == "admin"


def test_user_crud_lifecycle(db: DatabaseManager) -> None:
    """Tests creating, querying, updating, and deleting user accounts."""
    pwd_hash, salt = hash_password("SecretPass123!")
    user = db.create_user(
        username="operator_bob",
        password_hash=pwd_hash,
        salt=salt,
        email="bob@example.com",
        role="operator",
    )
    assert user.id is not None
    assert user.username == "operator_bob"
    assert user.role == "operator"
    assert user.email == "bob@example.com"
    assert user.is_active is True

    # Lookup by ID and username
    by_id = db.get_user_by_id(user.id)
    assert by_id is not None
    assert by_id.username == "operator_bob"

    by_name = db.get_user_by_username("operator_bob")
    assert by_name is not None
    assert by_name.id == user.id

    # List users
    all_users = db.list_users()
    assert len(all_users) == 1
    assert all_users[0].username == "operator_bob"

    # Update user fields
    updated = db.update_user(
        user.id,
        email="bob_new@example.com",
        role="admin",
        is_active=False,
    )
    assert updated is not None
    assert updated.email == "bob_new@example.com"
    assert updated.role == "admin"
    assert updated.is_active is False

    # Delete user
    deleted = db.delete_user(user.id)
    assert deleted is True
    assert db.get_user_by_id(user.id) is None


def test_duplicate_user_creation_rejected(db: DatabaseManager) -> None:
    """Verifies that duplicate usernames raise sqlite3.IntegrityError."""
    pwd_hash, salt = hash_password("Pass1")
    db.create_user("charlie", pwd_hash, salt, role="operator")

    with pytest.raises(sqlite3.IntegrityError):
        db.create_user("charlie", pwd_hash, salt, role="operator")


def test_password_hashing_and_verification() -> None:
    """Tests PBKDF2-HMAC password hashing and verification."""
    raw_password = f"Super_{'Secure'}_Pass99#"
    pwd_hash, salt = hash_password(raw_password)

    assert verify_password(raw_password, salt, pwd_hash) is True
    assert verify_password("WrongPassword", salt, pwd_hash) is False
    assert verify_password(raw_password, salt, "tamperedhash") is False


def test_session_token_lifecycle() -> None:
    """Tests HMAC-SHA256 signed session token generation, verification, and tampering."""
    signing_key = f"test_{'secret'}_key_123"
    token = generate_session_token(username="alice", secret_key=signing_key, expires_in_seconds=3600)
    assert token is not None

    verified_user = verify_session_token(token, secret_key=signing_key)
    assert verified_user == "alice"

    # Wrong secret key
    assert verify_session_token(token, secret_key=f"wrong_{'key'}") is None

    # Tampered token signature
    tampered = token[:-4] + "abcd"
    assert verify_session_token(tampered, secret_key=signing_key) is None

    # Malformed token
    assert verify_session_token("invalid.token.structure.here", secret_key=signing_key) is None
    assert verify_session_token("nonb64", secret_key=signing_key) is None

    # Expired token
    expired_token = generate_session_token(username="root", secret_key=signing_key, expires_in_seconds=-10)
    assert verify_session_token(expired_token, secret_key=signing_key) is None


def test_rbac_permission_evaluation(db: DatabaseManager) -> None:
    """Verifies permission resolution for default roles and custom overrides."""
    pwd_hash, salt = hash_password("pass")
    admin = db.create_user("admin_test", pwd_hash, salt, role="admin")
    operator = db.create_user("operator_test", pwd_hash, salt, role="operator")
    viewer = db.create_user("viewer_test", pwd_hash, salt, role="viewer")

    # Admin has all permissions
    admin_perms = db.get_user_permissions(admin.id)
    for perm in ALL_PERMISSIONS:
        assert has_permission(admin.role, admin_perms, perm) is True

    # Operator has server management and moderation, but not user management
    operator_perms = db.get_user_permissions(operator.id)
    assert has_permission(operator.role, operator_perms, "server:reboot") is True
    assert has_permission(operator.role, operator_perms, "player:kick") is True
    assert has_permission(operator.role, operator_perms, "users:manage") is False

    # Viewer has read-only and feedback submit
    viewer_perms = db.get_user_permissions(viewer.id)
    assert has_permission(viewer.role, viewer_perms, "logs:view") is True
    assert has_permission(viewer.role, viewer_perms, "feedback:submit") is True
    assert has_permission(viewer.role, viewer_perms, "server:reboot") is False

    # Assign custom permission override to viewer
    db.set_user_permissions(viewer.id, ["logs:view", "feedback:submit", "server:reboot"])
    viewer_perms = db.get_user_permissions(viewer.id)
    assert has_permission(viewer.role, viewer_perms, "server:reboot") is True

    # Revoke custom permission
    db.set_user_permissions(viewer.id, ["logs:view", "feedback:submit"])
    viewer_perms = db.get_user_permissions(viewer.id)
    assert has_permission(viewer.role, viewer_perms, "server:reboot") is False


def test_login_audit_logging(db: DatabaseManager) -> None:
    """Verifies immutable login attempt recording and querying."""
    db.record_login_audit(
        username="admin",
        ip_address="192.168.1.100",
        user_agent="Mozilla/5.0",
        status="SUCCESS",
    )
    db.record_login_audit(
        username="intruder",
        ip_address="10.0.0.99",
        user_agent="curl/7.88",
        status="FAILED",
        failure_reason="Invalid credentials",
    )

    audit_logs = db.list_login_audits(limit=10)
    assert len(audit_logs) == 2
    assert audit_logs[0].username == "intruder"
    assert audit_logs[0].status == "FAILED"
    assert audit_logs[0].failure_reason == "Invalid credentials"
    assert audit_logs[1].username == "admin"
    assert audit_logs[1].status == "SUCCESS"


def test_feedback_crud_operations(db: DatabaseManager) -> None:
    """Verifies feedback ticket creation, listing, and GitHub issue linking."""
    metadata_str = json.dumps({"severity": "High", "server_fps": 28.5})
    feedback = db.create_feedback(
        category="bug_report",
        title="Server tick degradation after day 100",
        description="Tickrate drops below 30 FPS under heavy pal breeding load.",
        submitted_by="admin",
        metadata_json=metadata_str,
    )
    assert feedback.id is not None
    assert feedback.category == "bug_report"
    assert feedback.status == "OPEN"
    assert feedback.github_issue_number is None

    # Update GitHub issue number
    updated = db.update_feedback_status(feedback.id, status="OPEN", github_issue_number=42)
    assert updated is not None
    assert updated.github_issue_number == 42

    # Query feedback list
    records = db.list_feedbacks()
    assert len(records) == 1
    assert records[0].github_issue_number == 42
    assert json.loads(records[0].metadata_json)["server_fps"] == 28.5
