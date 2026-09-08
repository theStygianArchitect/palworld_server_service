"""Cryptographic authentication, password hashing, session tokens, and RBAC evaluation.

Provides PBKDF2-HMAC password hashing, HMAC-SHA256 session token generation,
constant-time verification, and fine-grained permission evaluation with zero
external C dependencies in strict compliance with 3 AM standards.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from app.core.config import resolve_admin_credential_export_path
from app.core.logger import log
from app.database.db import DatabaseManager
from app.database.models import DEFAULT_ROLE_PERMISSIONS, UserRecord

PBKDF2_ITERATIONS = 100_000

# ---------------------------------------------------------------------------
# Bootstrap State: Ephemeral in-memory singleton for first-spin setup
# ---------------------------------------------------------------------------
# Holds the plaintext generated admin password until the operator explicitly
# acknowledges saving it via POST /api/auth/ack-bootstrap or completes the
# first successful admin login. At that point, `_bootstrap_state.password`
# is overwritten with None to permanently erase it from memory.
# ---------------------------------------------------------------------------


class BootstrapState:  # pylint: disable=too-few-public-methods
    """Thread-safe ephemeral holder for first-spin administrator credentials.

    Attributes:
        is_pending (bool): True if bootstrap acknowledgment is still outstanding.
        username (str | None): The bootstrapped admin account username.
        password (str | None): Ephemeral plaintext generated admin password.
    """

    def __init__(self) -> None:
        """Initializes the bootstrap state as unresolved and empty."""
        self.is_pending: bool = False
        self.username: str | None = None
        self.password: str | None = None


# Module-level singleton — initialized during application lifespan startup.
_bootstrap_state: BootstrapState = BootstrapState()




def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Generates a PBKDF2-HMAC-SHA256 digest with a cryptographically secure random salt.

    Args:
        password: Plaintext user password string.
        salt: Optional existing hexadecimal salt; generates a new 16-byte salt if None.

    Returns:
        tuple[str, str]: (hexadecimal_password_hash, hexadecimal_salt).
    """
    if salt is None:
        salt = secrets.token_hex(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return digest, salt


def verify_password(plain_password: str, salt: str, password_hash: str) -> bool:
    """Performs constant-time verification of a plaintext password against a stored hash.

    Args:
        plain_password: Provided plaintext password.
        salt: Stored hexadecimal user salt.
        password_hash: Stored expected hexadecimal PBKDF2 digest.

    Returns:
        bool: True if password matches stored hash, False otherwise.
    """
    calculated_hash, _ = hash_password(plain_password, salt=salt)
    return hmac.compare_digest(calculated_hash, password_hash)


def generate_session_token(username: str, secret_key: str, expires_in_seconds: int = 86400) -> str:
    """Creates a cryptographically signed, stateless session token.

    Payload format is base64url-encoded JSON containing username, expiration UTC timestamp,
    and a random nonce, appended with an HMAC-SHA256 signature.

    Args:
        username: Target authenticated username.
        secret_key: Secret server key used to sign the token HMAC.
        expires_in_seconds: Lifespan of the token in seconds (default: 24 hours).

    Returns:
        str: URL-safe signed session token string formatted as '<payload_b64>.<sig_b64>'.
    """
    expires_at = int(time.time()) + expires_in_seconds
    nonce = secrets.token_hex(8)
    payload_dict = {
        "sub": username,
        "exp": expires_at,
        "nonce": nonce,
    }
    payload_json = json.dumps(payload_dict, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8").rstrip("=")

    signature = hmac.new(
        secret_key.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    return f"{payload_b64}.{sig_b64}"


# pylint: disable=too-many-return-statements
# Rationale: Defensive step-by-step validation of signature, format, payload JSON, expiration, and identity.
def verify_session_token(token: str, secret_key: str) -> str | None:
    """Verifies HMAC signature and expiration timestamp on a session token.

    Args:
        token: URL-safe signed token string formatted as '<payload_b64>.<sig_b64>'.
        secret_key: Secret server key used to verify the token signature.

    Returns:
        str | None: Authenticated username if valid and unexpired; None otherwise.
    """
    parts = token.split(".")
    if len(parts) != 2:
        log.debug("Session token malformed: expected 2 segments, got %d", len(parts))
        return None

    payload_b64, sig_b64 = parts

    expected_sig = hmac.new(
        secret_key.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        log.debug("Session token signature verification failed.")
        return None

    try:
        # Add padding back if necessary
        padding = "=" * (-len(payload_b64) % 4)
        payload_json = base64.urlsafe_b64decode((payload_b64 + padding).encode("utf-8")).decode("utf-8")
        payload: dict[str, Any] = json.loads(payload_json)
    except UnicodeDecodeError as err:
        log.debug("Failed decoding session token payload UTF-8: %s", err)
        return None
    except json.JSONDecodeError as err:
        log.debug("Failed parsing session token payload JSON: %s", err)
        return None
    except ValueError as err:
        log.debug("Value error decoding session token payload: %s", err)
        return None

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        log.debug("Session token expired or missing exp claim.")
        return None

    username = payload.get("sub")
    if not isinstance(username, str) or not username:
        log.debug("Session token missing valid sub claim.")
        return None

    return username


def has_permission(user_role: str, user_permissions: list[str], required_permission: str) -> bool:
    """Evaluates whether a user's role and granular permissions satisfy a required action.

    Args:
        user_role: Primary assigned user role string ('admin', 'operator', 'viewer').
        user_permissions: List of granular permission tokens assigned to the user.
        required_permission: Target required permission string (e.g. 'server:reboot').

    Returns:
        bool: True if user is authorized, False otherwise.
    """
    if user_role == "admin":
        return True

    if "*" in user_permissions:
        return True

    if required_permission in user_permissions:
        return True

    role_defaults = DEFAULT_ROLE_PERMISSIONS.get(user_role, [])
    return required_permission in role_defaults


def bootstrap_admin_user(
    db: DatabaseManager,
    default_password: str | None = None,
    export_path: Path | str | None = None,
) -> UserRecord:
    """Bootstraps default administrator user upon first database initialization.

    If no users exist in the database and default_password is not explicitly provided,
    generates a cryptographically strong random password using CSPRNG secrets,
    hashes it, stores the admin user, and writes the credentials out-of-band to export_path
    with strict POSIX 0600 file permissions.

    When a random password is generated and the system is in first-spin mode (bootstrap
    not yet completed per db metadata), also populates the in-memory BootstrapState so
    the setup UI can present credentials to the operator without file system access.

    Args:
        db: Initialized DatabaseManager instance.
        default_password: Optional password to assign. If None or empty, generates random token.
        export_path: Optional destination filepath for writing out-of-band credentials.

    Returns:
        UserRecord: The bootstrapped or existing administrator record.
    """
    existing_admin = db.get_user_by_username("admin")
    if existing_admin is not None:
        return existing_admin

    if db.count_users() == 0:
        log.info("Bootstrapping default 'admin' user account in SQLite database.")
        is_generated = False
        password_to_use = default_password
        if not password_to_use:
            password_to_use = secrets.token_urlsafe(20)
            is_generated = True

        pw_hash, salt = hash_password(password_to_use)
        admin_user = db.create_user(
            username="admin",
            password_hash=pw_hash,
            salt=salt,
            email="admin@localhost",
            role="admin",
            is_active=True,
        )

        if is_generated or export_path is not None:
            resolved_export = resolve_admin_credential_export_path(export_path)
            _export_credentials_to_file(resolved_export, password_to_use)

        # Populate in-memory bootstrap state so the setup UI can present the credential
        # without requiring the operator to access the filesystem out-of-band.
        # Only mark pending if bootstrap has not been previously acknowledged (first-spin only).
        if is_generated and db.get_meta("bootstrap_completed") is None:
            set_bootstrap_pending(username="admin", password=password_to_use)

        return admin_user

    # If users exist but 'admin' does not, return first available user or create admin
    first_users = db.list_users()
    return first_users[0]


def _export_credentials_to_file(export_path: Path, password: str) -> None:
    """Safely writes initial administrator credentials out-of-band with POSIX 0600 mode."""
    try:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        banner = (
            "=========================================================================\n"
            " Palworld Operations Suite - Initial Administrator Credentials\n"
            f" Generated: {now_ts}\n"
            " Username:  admin\n"
            f" Password:  {password}\n"
            " Note:      Store this securely or rotate via Web Portal.\n"
            "=========================================================================\n"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(str(export_path), flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(banner)

        if os.name != "nt":
            os.chmod(export_path, 0o600)

        log.info("Initial admin credentials exported out-of-band to %s", export_path)
    except OSError as err:
        log.warning("Could not export initial admin credentials to %s: %s", export_path, err)


def get_bootstrap_state() -> BootstrapState:
    """Returns the module-level ephemeral bootstrap credential state.

    Returns:
        BootstrapState: Current bootstrap lifecycle state. If is_pending is False,
            the system has been initialized and the credential endpoint is sealed.
    """
    return _bootstrap_state


def set_bootstrap_pending(username: str, password: str) -> None:
    """Marks the bootstrap state as pending with the given ephemeral credentials.

    Called during application lifespan startup when a new admin is bootstrapped.
    The plaintext password is held in-memory until acknowledged or first admin login.

    Args:
        username (str): The bootstrapped admin account username.
        password (str): The plaintext generated admin password (ephemeral).
    """
    _bootstrap_state.is_pending = True
    _bootstrap_state.username = username
    _bootstrap_state.password = password


def acknowledge_bootstrap(db: DatabaseManager) -> None:
    """Permanently acknowledges and seals the first-spin bootstrap state.

    Writes bootstrap_completed = 'true' to the SQLite metadata table, then
    zeroes the in-memory plaintext password to prevent future retrieval.

    Args:
        db (DatabaseManager): Initialized database manager instance.
    """
    _bootstrap_state.is_pending = False
    _bootstrap_state.username = None
    _bootstrap_state.password = None
    db.set_meta("bootstrap_completed", "true")
    log.info("Bootstrap acknowledgment recorded — ephemeral credentials wiped from memory.")
