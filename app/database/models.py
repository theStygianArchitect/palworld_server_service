"""Data models and type definitions for the database and authentication domain.

Defines typed dataclasses and constant mappings for users, permissions,
login audits, and feedback submissions in strict compliance with 3 AM
type isolation standards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RoleType = Literal["admin", "operator", "viewer"]

ALL_PERMISSIONS: list[str] = [
    "server:reboot",
    "server:settings",
    "player:kick",
    "player:ban",
    "player:broadcast",
    "logs:view",
    "feedback:submit",
    "users:manage",
    "system:update",
]

DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": list(ALL_PERMISSIONS),
    "operator": [
        "server:reboot",
        "server:settings",
        "player:kick",
        "player:ban",
        "player:broadcast",
        "logs:view",
        "feedback:submit",
        "system:update",
    ],
    "viewer": [
        "logs:view",
        "feedback:submit",
    ],
}


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
# Rationale: Relational database record mapping requires storing all user schema columns in a single dataclass.
class UserRecord:
    """Represents an authenticated system user record.

    Attributes:
        id: Primary key unique identifier.
        username: Unique login handle string.
        password_hash: Hexadecimal PBKDF2 hash digest.
        salt: Unique per-user random cryptographic salt string.
        email: Contact email address.
        role: Primary assigned role identifier (admin, operator, viewer).
        is_active: Whether account is active and permitted to authenticate.
        created_at: ISO-8601 UTC creation timestamp string.
        last_login: ISO-8601 UTC last successful login timestamp or None.
    """

    id: int
    username: str
    password_hash: str
    salt: str
    email: str
    role: str
    is_active: bool
    created_at: str
    last_login: str | None = None


@dataclass(frozen=True)
class UserPermissionRecord:
    """Represents a fine-grained permission override assigned to a user.

    Attributes:
        id: Primary key unique identifier.
        user_id: Foreign key referencing the parent user record.
        permission: Specific granular permission string token.
    """

    id: int
    user_id: int
    permission: str


@dataclass(frozen=True)
class LoginAuditRecord:
    """Represents an immutable login attempt audit trail entry.

    Attributes:
        id: Primary key unique identifier.
        username: Attempted login username.
        timestamp: ISO-8601 UTC timestamp of the login event.
        ip_address: Source IPv4 or IPv6 client address.
        user_agent: Inbound HTTP client User-Agent string.
        status: Attempt outcome ('SUCCESS' or 'FAILED').
        failure_reason: Diagnostic reason if status is FAILED.
    """

    id: int
    username: str
    timestamp: str
    ip_address: str
    user_agent: str
    status: str
    failure_reason: str = ""


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
# Rationale: Relational database record mapping requires storing all feedback schema columns in a single dataclass.
class FeedbackRecord:
    """Represents an issue or feedback ticket submission.

    Attributes:
        id: Primary key unique identifier.
        category: Template category (bug_report, feature_request, etc.).
        title: Short descriptive title summary.
        description: Markdown formatted body content.
        metadata_json: Serialized JSON dictionary of template-specific fields.
        submitted_by: Submitter username or client handle.
        status: Ticket lifecycle status ('OPEN', 'RESOLVED', 'CLOSED').
        github_issue_number: Linked GitHub issue number if exported, else None.
        created_at: ISO-8601 UTC timestamp of submission.
    """

    id: int
    category: str
    title: str
    description: str
    metadata_json: str
    submitted_by: str
    status: str
    github_issue_number: int | None
    created_at: str
