"""SQLite database domain package exports.

Exports database management engine, data records, and authentication utilities.
"""

from __future__ import annotations

from app.database.auth import (
    BootstrapState,
    acknowledge_bootstrap,
    bootstrap_admin_user,
    generate_session_token,
    get_bootstrap_state,
    has_permission,
    hash_password,
    set_bootstrap_pending,
    verify_password,
    verify_session_token,
)
from app.database.db import DatabaseManager
from app.database.metric_models import (
    Metric30DaySummary,
    MetricBucketRecord,
    MetricSnapshotRecord,
)
from app.database.metrics_db import MetricsDatabaseManager
from app.database.models import (
    ALL_PERMISSIONS,
    DEFAULT_ROLE_PERMISSIONS,
    FeedbackRecord,
    LoginAuditRecord,
    RoleType,
    UserPermissionRecord,
    UserRecord,
)

__all__ = [
    "ALL_PERMISSIONS",
    "DEFAULT_ROLE_PERMISSIONS",
    "BootstrapState",
    "DatabaseManager",
    "FeedbackRecord",
    "LoginAuditRecord",
    "Metric30DaySummary",
    "MetricBucketRecord",
    "MetricSnapshotRecord",
    "MetricsDatabaseManager",
    "RoleType",
    "UserPermissionRecord",
    "UserRecord",
    "acknowledge_bootstrap",
    "bootstrap_admin_user",
    "generate_session_token",
    "get_bootstrap_state",
    "has_permission",
    "hash_password",
    "set_bootstrap_pending",
    "verify_password",
    "verify_session_token",
]
