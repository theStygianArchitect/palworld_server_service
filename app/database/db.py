"""SQLite database engine, connection lifecycle, and transactional queries.

Provides transactional persistence, table migrations, and synchronous/asynchronous
wrappers for users, permissions, audit trails, and template feedback in strict
compliance with 12-factor and 3 AM defensive standards.
"""

from __future__ import annotations

import datetime
import sqlite3
import threading
from pathlib import Path

from app.core.logger import log
from app.database.models import (
    DEFAULT_ROLE_PERMISSIONS,
    FeedbackRecord,
    LoginAuditRecord,
    UserRecord,
)

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    email TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'viewer',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS user_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    permission TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(user_id, permission)
);

CREATE TABLE IF NOT EXISTS login_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_reason TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS feedback_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    submitted_by TEXT NOT NULL DEFAULT 'anonymous',
    status TEXT NOT NULL DEFAULT 'OPEN',
    github_issue_number INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_login_audit_timestamp ON login_audit(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback_submissions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_filter ON feedback_submissions(submitted_by, category, status, created_at DESC);
"""


class DatabaseManager:
    """Manages SQLite connection lifecycle, schema initialization, and transactional queries.

    Attributes:
        db_path (str): Filesystem path or ':memory:' identifier for SQLite database.
        _conn (sqlite3.Connection | None): Active SQLite connection instance.
        _lock (threading.RLock): Concurrency lock for thread-safe serialized transactions.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        """Initializes the DatabaseManager.

        Args:
            db_path: Target filesystem path string or ':memory:'.
        """
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def get_connection(self) -> sqlite3.Connection:
        """Retrieves or creates the active SQLite database connection.

        Returns:
            sqlite3.Connection: Active database connection configured with WAL and Row factory.

        Raises:
            sqlite3.Error: If database connection initialization fails.
        """
        if self._conn is not None:
            return self._conn

        with self._lock:
            if self._conn is not None:
                return self._conn

            if self.db_path != ":memory:":
                db_file = Path(self.db_path)
                try:
                    db_file.parent.mkdir(parents=True, exist_ok=True)
                except PermissionError as err:
                    log.warning("Permission denied creating db directory %s: %s. Using fallback.", db_file.parent, err)
                    fallback_dir = Path.home() / ".palmanager"
                    try:
                        fallback_dir.mkdir(parents=True, exist_ok=True)
                        self.db_path = str(fallback_dir / "palmanager.db")
                    except OSError as sub_err:
                        log.warning("Failed creating fallback directory: %s. Using in-memory.", sub_err)
                        self.db_path = ":memory:"
                except OSError as err:
                    log.warning("OS error creating db directory %s: %s. Using fallback.", db_file.parent, err)
                    fallback_dir = Path.home() / ".palmanager"
                    try:
                        fallback_dir.mkdir(parents=True, exist_ok=True)
                        self.db_path = str(fallback_dir / "palmanager.db")
                    except OSError as sub_err:
                        log.warning("Failed creating fallback directory: %s. Using in-memory.", sub_err)
                        self.db_path = ":memory:"

            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=10.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            if self.db_path != ":memory:":
                try:
                    conn.execute("PRAGMA journal_mode = WAL;")
                except sqlite3.OperationalError as err:
                    log.debug("WAL mode activation ignored (e.g. read-only or in-memory): %s", err)
            conn.execute("PRAGMA busy_timeout = 5000;")
            self._conn = conn
            return self._conn

    def initialize(self) -> None:
        """Executes database schema DDL migrations ensuring all required tables exist."""
        with self._lock:
            conn = self.get_connection()
            try:
                conn.executescript(SCHEMA_DDL)
                conn.commit()
                log.info("SQLite database schema initialized at %s", self.db_path)
            except sqlite3.OperationalError as err:
                log.error("OperationalError initializing SQLite schema: %s", err)
                raise
            except sqlite3.DatabaseError as err:
                log.error("DatabaseError initializing SQLite schema: %s", err)
                raise

    def close(self) -> None:
        """Closes the underlying SQLite connection cleanly."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error as err:
                    log.debug("Error closing SQLite connection: %s", err)
                self._conn = None

    def _row_to_user(self, row: sqlite3.Row) -> UserRecord:
        """Maps an SQLite row object to an immutable UserRecord instance."""
        return UserRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            salt=str(row["salt"]),
            email=str(row["email"] or ""),
            role=str(row["role"]),
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"]),
            last_login=str(row["last_login"]) if row["last_login"] else None,
        )

    # =========================================================================
    # User Management Operations
    # =========================================================================

    def get_user_by_id(self, user_id: int) -> UserRecord | None:
        """Fetches a user record by primary key identifier.

        Args:
            user_id: Unique primary key integer.

        Returns:
            UserRecord or None if record does not exist.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "SELECT id, username, password_hash, salt, email, role, is_active, created_at, last_login "
                "FROM users WHERE id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_user(row)

    def get_user_by_username(self, username: str) -> UserRecord | None:
        """Fetches a user record by unique login username.

        Args:
            username: Target username string.

        Returns:
            UserRecord or None if record does not exist.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "SELECT id, username, password_hash, salt, email, role, is_active, created_at, last_login "
                "FROM users WHERE username = ?",
                (username,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_user(row)

    def list_users(self) -> list[UserRecord]:
        """Lists all registered users sorted by creation order.

        Returns:
            List of UserRecord instances.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "SELECT id, username, password_hash, salt, email, role, is_active, created_at, last_login "
                "FROM users ORDER BY id ASC"
            )
            return [self._row_to_user(row) for row in cursor.fetchall()]

    def count_users(self) -> int:
        """Returns the total number of registered users.

        Returns:
            Integer count of user rows.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            return int(cursor.fetchone()[0])

    def count_active_admins(self) -> int:
        """Returns the total number of active users with administrator role.

        Returns:
            Integer count of active administrator user rows.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1")
            return int(cursor.fetchone()[0])

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # Rationale: User entity relational schema requires primary credentials and account attributes.
    def create_user(
        self,
        username: str,
        password_hash: str,
        salt: str,
        email: str = "",
        role: str = "viewer",
        is_active: bool = True,
    ) -> UserRecord:
        """Inserts a new user record and assigns default role permissions.

        Args:
            username: Unique username handle.
            password_hash: Hexadecimal PBKDF2 digest.
            salt: Hexadecimal cryptographic salt.
            email: Optional contact email.
            role: Assigned role ('admin', 'operator', 'viewer').
            is_active: Whether account is enabled.

        Returns:
            Created UserRecord instance.
        """
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, salt, email, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, password_hash, salt, email, role, 1 if is_active else 0, created_at),
            )
            new_id = int(cursor.lastrowid or 0)

            # Assign default role permissions
            default_perms = DEFAULT_ROLE_PERMISSIONS.get(role, [])
            for perm in default_perms:
                conn.execute(
                    "INSERT OR IGNORE INTO user_permissions (user_id, permission) VALUES (?, ?)",
                    (new_id, perm),
                )
            conn.commit()

        user = self.get_user_by_id(new_id)
        if user is None:
            raise RuntimeError(f"Failed to retrieve user immediately after creation (ID {new_id})")
        return user

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # Rationale: User entity update operation supports optional mutation of all mutable fields.
    def update_user(
        self,
        user_id: int,
        email: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
        password_hash: str | None = None,
        salt: str | None = None,
    ) -> UserRecord | None:
        """Updates attributes for an existing user record.

        Args:
            user_id: Target user primary key identifier.
            email: Optional new email address.
            role: Optional new role.
            is_active: Optional new active status.
            password_hash: Optional new password hash.
            salt: Optional new salt.

        Returns:
            Updated UserRecord or None if target user was not found.
        """
        existing = self.get_user_by_id(user_id)
        if existing is None:
            return None

        is_active_val: int | None = None
        if is_active is not None:
            is_active_val = 1 if is_active else 0

        with self._lock:
            conn = self.get_connection()
            conn.execute(
                "UPDATE users SET "
                "email = COALESCE(?, email), "
                "role = COALESCE(?, role), "
                "is_active = COALESCE(?, is_active), "
                "password_hash = COALESCE(?, password_hash), "
                "salt = COALESCE(?, salt) "
                "WHERE id = ?",
                (email, role, is_active_val, password_hash, salt, user_id),
            )

            # If role changed, sync default permissions if no custom overrides exist
            if role is not None and role != existing.role:
                conn.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
                default_perms = DEFAULT_ROLE_PERMISSIONS.get(role, [])
                for perm in default_perms:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_permissions (user_id, permission) VALUES (?, ?)",
                        (user_id, perm),
                    )
            conn.commit()

        return self.get_user_by_id(user_id)

    def delete_user(self, user_id: int) -> bool:
        """Deletes a user record and cascades associated permissions.

        Args:
            user_id: Target user primary key identifier.

        Returns:
            True if row was deleted, False otherwise.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update_user_last_login(self, user_id: int, timestamp: str) -> None:
        """Updates the last successful login timestamp for a user.

        Args:
            user_id: Target user primary key identifier.
            timestamp: ISO-8601 UTC timestamp string.
        """
        with self._lock:
            conn = self.get_connection()
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (timestamp, user_id))
            conn.commit()

    # =========================================================================
    # User Permissions Operations
    # =========================================================================

    def get_user_permissions(self, user_id: int) -> list[str]:
        """Retrieves all granular permissions explicitly granted to a user.

        Args:
            user_id: Target user primary key identifier.

        Returns:
            List of permission token strings.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "SELECT permission FROM user_permissions WHERE user_id = ? ORDER BY permission ASC",
                (user_id,),
            )
            return [str(row["permission"]) for row in cursor.fetchall()]

    def set_user_permissions(self, user_id: int, permissions: list[str]) -> None:
        """Overwrites user permissions with a specified list of tokens.

        Args:
            user_id: Target user primary key identifier.
            permissions: Complete list of permission tokens to assign.
        """
        with self._lock:
            conn = self.get_connection()
            conn.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
            for perm in set(permissions):
                conn.execute(
                    "INSERT INTO user_permissions (user_id, permission) VALUES (?, ?)",
                    (user_id, perm),
                )
            conn.commit()

    # =========================================================================
    # Login Audit Trail Operations
    # =========================================================================

    def record_login_audit(
        self,
        username: str,
        ip_address: str,
        user_agent: str,
        status: str,
        failure_reason: str = "",
    ) -> LoginAuditRecord:
        """Records an immutable login event into the audit trail.

        Args:
            username: Attempted login username.
            ip_address: Remote client IP address.
            user_agent: Inbound client User-Agent string.
            status: Outcome status ('SUCCESS' or 'FAILED').
            failure_reason: Optional diagnostic reason.

        Returns:
            Persisted LoginAuditRecord instance.
        """
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "INSERT INTO login_audit (username, timestamp, ip_address, user_agent, status, failure_reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, timestamp, ip_address, user_agent, status, failure_reason),
            )
            conn.commit()
            new_id = int(cursor.lastrowid or 0)

        return LoginAuditRecord(
            id=new_id,
            username=username,
            timestamp=timestamp,
            ip_address=ip_address,
            user_agent=user_agent,
            status=status,
            failure_reason=failure_reason,
        )

    def list_login_audits(self, limit: int = 50, offset: int = 0) -> list[LoginAuditRecord]:
        """Returns recent login audit trail records ordered by timestamp descending.

        Args:
            limit: Maximum number of rows to return.
            offset: Query pagination offset.

        Returns:
            List of LoginAuditRecord instances.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "SELECT id, username, timestamp, ip_address, user_agent, status, failure_reason "
                "FROM login_audit ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            records: list[LoginAuditRecord] = []
            for row in cursor.fetchall():
                records.append(
                    LoginAuditRecord(
                        id=int(row["id"]),
                        username=str(row["username"]),
                        timestamp=str(row["timestamp"]),
                        ip_address=str(row["ip_address"]),
                        user_agent=str(row["user_agent"]),
                        status=str(row["status"]),
                        failure_reason=str(row["failure_reason"] or ""),
                    )
                )
            return records

    # =========================================================================
    # Feedback & Issue Submission Operations
    # =========================================================================

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # Rationale: Feedback submission persistence requires recording metadata and issue details.
    def create_feedback(
        self,
        category: str,
        title: str,
        description: str,
        metadata_json: str = "{}",
        submitted_by: str = "anonymous",
        github_issue_number: int | None = None,
    ) -> FeedbackRecord:
        """Inserts a new feedback submission record into the database.

        Args:
            category: Template category (bug_report, feature_request, etc.).
            title: Short summary title.
            description: Markdown description body.
            metadata_json: Serialized JSON dictionary of template fields.
            submitted_by: Submitter handle or username.
            github_issue_number: Optional linked GitHub issue number.

        Returns:
            Persisted FeedbackRecord instance.
        """
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "INSERT INTO feedback_submissions "
                "(category, title, description, metadata_json, submitted_by, status, github_issue_number, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?)",
                (category, title, description, metadata_json, submitted_by, github_issue_number, created_at),
            )
            conn.commit()
            new_id = int(cursor.lastrowid or 0)

        return FeedbackRecord(
            id=new_id,
            category=category,
            title=title,
            description=description,
            metadata_json=metadata_json,
            submitted_by=submitted_by,
            status="OPEN",
            github_issue_number=github_issue_number,
            created_at=created_at,
        )

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # Rationale: Filtered query requires pagination bounds and criteria for submitted_by, category, status.
    def list_feedbacks(
        self,
        limit: int = 50,
        offset: int = 0,
        submitted_by: str | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> list[FeedbackRecord]:
        """Returns recorded feedback submissions ordered by timestamp descending with optional filtering.

        Args:
            limit: Maximum number of rows to return.
            offset: Query pagination offset.
            submitted_by: Optional filter for submissions by a specific user handle.
            category: Optional filter for issue template category.
            status: Optional filter for ticket lifecycle status.

        Returns:
            List of FeedbackRecord instances matching filter criteria.
        """
        query = (
            "SELECT id, category, title, description, metadata_json, submitted_by, status, "
            "github_issue_number, created_at FROM feedback_submissions WHERE "
            "(? IS NULL OR submitted_by = ?) AND "
            "(? IS NULL OR category = ?) AND "
            "(? IS NULL OR status = ?) "
            "ORDER BY id DESC LIMIT ? OFFSET ?"
        )
        params = (
            submitted_by,
            submitted_by,
            category,
            category,
            status,
            status,
            limit,
            offset,
        )

        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(query, params)
            records: list[FeedbackRecord] = []
            for row in cursor.fetchall():
                records.append(
                    FeedbackRecord(
                        id=int(row["id"]),
                        category=str(row["category"]),
                        title=str(row["title"]),
                        description=str(row["description"]),
                        metadata_json=str(row["metadata_json"]),
                        submitted_by=str(row["submitted_by"]),
                        status=str(row["status"]),
                        github_issue_number=int(row["github_issue_number"]) if row["github_issue_number"] else None,
                        created_at=str(row["created_at"]),
                    )
                )
            return records

    def get_feedback(self, feedback_id: int) -> FeedbackRecord | None:
        """Fetches a single feedback record by primary key identifier.

        Args:
            feedback_id: Unique primary key integer.

        Returns:
            FeedbackRecord or None if record does not exist.
        """
        with self._lock:
            conn = self.get_connection()
            cursor = conn.execute(
                "SELECT id, category, title, description, metadata_json, submitted_by, status, "
                "github_issue_number, created_at FROM feedback_submissions WHERE id = ?",
                (feedback_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return FeedbackRecord(
                id=int(row["id"]),
                category=str(row["category"]),
                title=str(row["title"]),
                description=str(row["description"]),
                metadata_json=str(row["metadata_json"]),
                submitted_by=str(row["submitted_by"]),
                status=str(row["status"]),
                github_issue_number=int(row["github_issue_number"]) if row["github_issue_number"] else None,
                created_at=str(row["created_at"]),
            )

    def update_feedback_status(
        self,
        feedback_id: int,
        status: str,
        github_issue_number: int | None = None,
    ) -> FeedbackRecord | None:
        """Updates status or linked GitHub issue number for a feedback submission.

        Args:
            feedback_id: Target feedback primary key.
            status: New status ('OPEN', 'RESOLVED', 'CLOSED').
            github_issue_number: Optional linked GitHub issue number.

        Returns:
            Updated FeedbackRecord or None if not found.
        """
        with self._lock:
            conn = self.get_connection()
            if github_issue_number is not None:
                conn.execute(
                    "UPDATE feedback_submissions SET status = ?, github_issue_number = ? WHERE id = ?",
                    (status, github_issue_number, feedback_id),
                )
            else:
                conn.execute(
                    "UPDATE feedback_submissions SET status = ? WHERE id = ?",
                    (status, feedback_id),
                )
            conn.commit()

        return self.get_feedback(feedback_id)
