"""Authentication and User management router.

Provides endpoints for bootstrapping, authentication, and user RBAC.
"""

from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.schemas import (
    BootstrapAckResponse,
    BootstrapCredentialsResponse,
    LoginAuditResponse,
    UserCreateRequest,
    UserLoginRequest,
    UserLoginResponse,
    UserRegisterRequest,
    UserResponse,
    UserRoleUpdateRequest,
    UserUpdateRequest,
)
from app.core.logger import log
from app.database import (
    UserRecord,
    acknowledge_bootstrap,
    generate_session_token,
    get_bootstrap_state,
    hash_password,
    verify_password,
)
from app.routers.deps import db, get_client_ip, get_current_user, perm_users_manage, settings

router = APIRouter(tags=["Authentication & Users"])


@router.get("/api/auth/bootstrap-credentials", response_model=BootstrapCredentialsResponse)
async def get_bootstrap_credentials(
    _request: Request,
) -> BootstrapCredentialsResponse:
    """Returns the ephemeral first-spin admin credentials if bootstrap is still pending.

    This endpoint is **unauthenticated** by design so operators can retrieve credentials
    on a fresh install without logging in first. Once POST /api/auth/ack-bootstrap is called
    (or the first admin login occurs), this endpoint permanently returns 404.

    The response includes the plaintext password **exactly once**. After acknowledgment,
    the password is wiped from memory and cannot be recovered from this API.

    Returns:
        BootstrapCredentialsResponse: Current bootstrap lifecycle state with ephemeral password.

    Raises:
        HTTPException: 404 if bootstrap has already been acknowledged.
    """
    state = get_bootstrap_state()
    if not state.is_pending:
        raise HTTPException(
            status_code=404,
            detail="System setup is complete. Bootstrap credentials are no longer available.",
        )
    return BootstrapCredentialsResponse(
        is_pending=True,
        username=state.username,
        password=state.password,
        message=(
            "⚠️ FIRST-SPIN SETUP: Save this password now — it will never be shown again after you click 'I Saved It'. "
            "You can also find it at /etc/palmanager/initial_admin_credential.txt on the server."
        ),
    )


@router.post("/api/auth/ack-bootstrap", response_model=BootstrapAckResponse)
async def acknowledge_bootstrap_credentials(request: Request) -> BootstrapAckResponse:
    """Acknowledges the first-spin bootstrap credentials and permanently seals them.

    After this call, GET /api/auth/bootstrap-credentials returns 404 forever and the
    ephemeral plaintext password is wiped from server memory. This action is irreversible.

    This endpoint is **unauthenticated** because it is called from the setup modal before
    the operator has logged in. Authorization is implicit — calling this endpoint means the
    operator has confirmed they have saved the password.

    Args:
        request: Inbound FastAPI HTTP request.

    Returns:
        BootstrapAckResponse: Confirmation that credentials are permanently sealed.

    Raises:
        HTTPException: 409 if bootstrap was already acknowledged.
    """
    state = get_bootstrap_state()
    if not state.is_pending:
        raise HTTPException(
            status_code=409,
            detail="Bootstrap has already been acknowledged. Nothing to confirm.",
        )
    await asyncio.to_thread(acknowledge_bootstrap, db)
    log.info("Bootstrap acknowledged by client at %s", request.client)
    return BootstrapAckResponse(
        status="success",
        message=(
            "Initial credentials have been acknowledged and permanently wiped from memory. "
            "Please log in with your saved administrator password."
        ),
    )


@router.post("/api/auth/login", response_model=UserLoginResponse)
async def login(req: UserLoginRequest, request: Request, response: Response) -> UserLoginResponse:
    """Authenticates user credentials, writes an audit record, and issues a session token.

    Args:
        req: Login credentials.
        request: FastAPI HTTP request.
        response: FastAPI HTTP response.

    Returns:
        UserLoginResponse with token and granted permissions.

    Raises:
        HTTPException: 401 Unauthorized if credentials fail.
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "Unknown")

    user = db.get_user_by_username(req.username)
    if user is None or not user.is_active or not verify_password(req.password, user.salt, user.password_hash):
        reason = "Account disabled" if (user and not user.is_active) else "Invalid credentials"
        db.record_login_audit(
            username=req.username,
            ip_address=client_ip,
            user_agent=user_agent,
            status="FAILED",
            failure_reason=reason,
        )
        log.warning("Login failed for user '%s' from %s: %s", req.username, client_ip, reason)
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = generate_session_token(user.username, secret_key=settings.AdminPassword)
    db.record_login_audit(
        username=user.username,
        ip_address=client_ip,
        user_agent=user_agent,
        status="SUCCESS",
    )
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.update_user_last_login(user.id, now_iso)

    bootstrap_state = get_bootstrap_state()
    if bootstrap_state.is_pending and user.role == "admin":
        await asyncio.to_thread(acknowledge_bootstrap, db)
        log.info("Bootstrap auto-acknowledged on first admin login by '%s'.", user.username)

    response.set_cookie(
        key="pal_session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )

    permissions = db.get_user_permissions(user.id)
    return UserLoginResponse(
        status="success",
        token=token,
        username=user.username,
        role=user.role,
        permissions=permissions,
    )


@router.post("/api/auth/register", response_model=UserResponse)
async def register(
    req: UserRegisterRequest,
    request: Request,
) -> UserResponse:
    """Public self-service user registration assigning least-privilege viewer role.

    Args:
        req: Validated user registration request payload.
        request: FastAPI HTTP request for client IP audit logging.

    Returns:
        UserResponse: Created UserResponse record.

    Raises:
        HTTPException: 409 Conflict if username is already registered.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    existing = db.get_user_by_username(req.username)
    if existing is not None:
        log.warning("Registration conflict: username '%s' already exists (client %s)", req.username, client_ip)
        raise HTTPException(status_code=409, detail=f"Username '{req.username}' already registered.")

    pw_hash, salt = hash_password(req.password)
    user = db.create_user(
        username=req.username,
        password_hash=pw_hash,
        salt=salt,
        email=req.email,
        role="viewer",
        is_active=True,
    )
    log.info("Registered new user '%s' with role 'viewer' from %s", user.username, client_ip)
    perms = db.get_user_permissions(user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        permissions=perms,
    )


@router.post("/api/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    """Terminates session by clearing session cookie.

    Args:
        response: FastAPI HTTP response.

    Returns:
        dict[str, str]: Confirmation dictionary.
    """
    response.delete_cookie("pal_session_token")
    return {"status": "success", "message": "Successfully logged out."}


@router.get("/api/auth/me", response_model=UserResponse)
async def get_me(user: UserRecord = Depends(get_current_user)) -> UserResponse:
    """Returns profile and active permissions for the calling user.

    Args:
        user: Authenticated user record.

    Returns:
        UserResponse: UserResponse with role and permissions.
    """
    permissions = db.get_user_permissions(user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        permissions=permissions,
    )


@router.get("/api/auth/audit", response_model=list[LoginAuditResponse])
async def get_login_audit_trail(
    limit: int = 50,
    offset: int = 0,
    _: UserRecord = Depends(perm_users_manage),
) -> list[LoginAuditResponse]:
    """Retrieves paginated login attempts from the audit trail.

    Args:
        limit: Number of audit records to retrieve.
        offset: Query offset.
        _: Enforces users:manage permission.

    Returns:
        list[LoginAuditResponse]: List of LoginAuditResponse items.
    """
    records = db.list_login_audits(limit=limit, offset=offset)
    return [
        LoginAuditResponse(
            id=rec.id,
            username=rec.username,
            timestamp=rec.timestamp,
            ip_address=rec.ip_address,
            user_agent=rec.user_agent,
            status=rec.status,
            failure_reason=rec.failure_reason,
        )
        for rec in records
    ]


@router.get("/api/users", response_model=list[UserResponse])
async def list_registered_users(
    _: UserRecord = Depends(perm_users_manage),
) -> list[UserResponse]:
    """Lists all registered system user accounts.

    Args:
        _: Enforces users:manage permission.

    Returns:
        list[UserResponse]: List of UserResponse items.
    """
    users = db.list_users()
    response_list: list[UserResponse] = []
    for u in users:
        perms = db.get_user_permissions(u.id)
        response_list.append(
            UserResponse(
                id=u.id,
                username=u.username,
                email=u.email,
                role=u.role,
                is_active=u.is_active,
                created_at=u.created_at,
                last_login=u.last_login,
                permissions=perms,
            )
        )
    return response_list


@router.post("/api/users", response_model=UserResponse)
async def create_new_user(
    req: UserCreateRequest,
    _: UserRecord = Depends(perm_users_manage),
) -> UserResponse:
    """Creates a new user account with hashed password and assigned permissions.

    Args:
        req: User creation request.
        _: Enforces users:manage permission.

    Returns:
        UserResponse: Created UserResponse record.

    Raises:
        HTTPException: 409 Conflict if username already exists.
    """
    existing = db.get_user_by_username(req.username)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Username '{req.username}' already registered.")

    pw_hash, salt = hash_password(req.password)
    user = db.create_user(
        username=req.username,
        password_hash=pw_hash,
        salt=salt,
        email=req.email,
        role=req.role,
        is_active=True,
    )
    if req.permissions is not None:
        db.set_user_permissions(user.id, req.permissions)

    perms = db.get_user_permissions(user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        permissions=perms,
    )


@router.patch("/api/users/{user_id}", response_model=UserResponse)
async def update_existing_user(
    user_id: int,
    req: UserUpdateRequest,
    _: UserRecord = Depends(perm_users_manage),
) -> UserResponse:
    """Updates profile attributes, role, or credentials for an existing user.

    Args:
        user_id: Target user identifier.
        req: User update request.
        _: Enforces users:manage permission.

    Returns:
        UserResponse: Updated UserResponse record.

    Raises:
        HTTPException: 404 Not Found if user does not exist.
        HTTPException: 400 Bad Request if demoting/deactivating last admin.
    """
    existing = db.get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if existing.role == "admin" and existing.is_active:
        if req.role is not None and req.role != "admin" and db.count_active_admins() <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last remaining active administrator.")
        if req.is_active is False and db.count_active_admins() <= 1:
            raise HTTPException(status_code=400, detail="Cannot deactivate the last remaining active administrator.")

    pw_hash: str | None = None
    salt: str | None = None
    if req.password:
        pw_hash, salt = hash_password(req.password)

    updated = db.update_user(
        user_id=user_id,
        email=req.email,
        role=req.role,
        is_active=req.is_active,
        password_hash=pw_hash,
        salt=salt,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if req.permissions is not None:
        db.set_user_permissions(user_id, req.permissions)

    perms = db.get_user_permissions(user_id)
    return UserResponse(
        id=updated.id,
        username=updated.username,
        email=updated.email,
        role=updated.role,
        is_active=updated.is_active,
        created_at=updated.created_at,
        last_login=updated.last_login,
        permissions=perms,
    )


@router.patch("/api/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: int,
    req: UserRoleUpdateRequest,
    _: UserRecord = Depends(perm_users_manage),
) -> UserResponse:
    """Promotes or demotes an existing user account to a designated system role.

    Args:
        user_id: Target user identifier.
        req: Validated role update payload.
        _: Enforces users:manage administrative permission.

    Returns:
        UserResponse: Updated UserResponse record with synchronized permissions.

    Raises:
        HTTPException: 404 Not Found if user does not exist.
        HTTPException: 400 Bad Request if demoting the last remaining active administrator.
    """
    existing = db.get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if existing.role == "admin" and existing.is_active and req.role != "admin" and db.count_active_admins() <= 1:
        raise HTTPException(status_code=400, detail="Cannot demote the last remaining active administrator.")

    updated = db.update_user(user_id=user_id, role=req.role)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    perms = db.get_user_permissions(user_id)
    log.info("Updated role for user '%s' (ID %d) to '%s'", updated.username, user_id, req.role)
    return UserResponse(
        id=updated.id,
        username=updated.username,
        email=updated.email,
        role=updated.role,
        is_active=updated.is_active,
        created_at=updated.created_at,
        last_login=updated.last_login,
        permissions=perms,
    )


@router.delete("/api/users/{user_id}")
async def delete_existing_user(
    user_id: int,
    admin_user: UserRecord = Depends(perm_users_manage),
) -> dict[str, str]:
    """Deletes a user account from the system.

    Args:
        user_id: Target user identifier.
        admin_user: Currently authenticated administrator.

    Returns:
        dict[str, str]: Confirmation dictionary.

    Raises:
        HTTPException: 400 Bad Request if deleting self or the last administrator, or 404 if not found.
    """
    if admin_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own active administrator account.")

    existing = db.get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    if existing.role == "admin" and existing.is_active and db.count_active_admins() <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last remaining active administrator.")

    ok = db.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"User ID {user_id} not found.")

    return {"status": "success", "message": f"User ID {user_id} deleted."}
