from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.auth import generate_session_token, hash_password, session_expiry, verify_password
from pyxie_core.mail import send_email
from pyxie_core.models import AppSettings, Session as SessionModel, User

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVITE_TTL_DAYS = 7


class BootstrapRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class InviteRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None
    is_admin: bool = False
    # The browser's own origin (window.location.origin) -- the backend
    # doesn't reliably know its own public-facing URL (could be behind any
    # domain/proxy), so the frontend supplies it the same way it already
    # builds the copy-paste link client-side. None skips the email attempt
    # entirely (falls back to copy-paste only).
    invite_base_url: str | None = None


class ReinviteRequest(BaseModel):
    invite_base_url: str | None = None


class AcceptInviteRequest(BaseModel):
    token: str
    password: str


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    is_active: bool | None = None


class MeUpdate(BaseModel):
    display_name: str | None = None
    current_password: str | None = None
    new_password: str | None = None


def _user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "pending_invite": user.password_hash is None,
    }


@router.get("/bootstrap-status")
def bootstrap_status(db: Session = Depends(get_db)):
    needs_bootstrap = db.query(User).count() == 0
    return {"needs_bootstrap": needs_bootstrap}


@router.post("/bootstrap")
def bootstrap(payload: BootstrapRequest, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        raise HTTPException(status_code=409, detail="An administrator account already exists")
    if len(payload.password) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters")

    user = User(
        email=payload.email,
        display_name=payload.display_name or payload.email.split("@")[0],
        password_hash=hash_password(payload.password),
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.bootstrap.completed",
        actor=user.email,
        actor_type="user",
        result="success",
    )
    return {"id": str(user.id), "email": user.email}


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email, User.is_active.is_(True)).one_or_none()
    if user is None or user.password_hash is None or not verify_password(payload.password, user.password_hash):
        write_audit_event(
            db,
            event_category="auth",
            event_type="auth.login.failed",
            actor=payload.email,
            actor_type="user",
            result="failure",
            severity="warning",
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = generate_session_token()
    session = SessionModel(
        id=token,
        user_id=user.id,
        expires_at=session_expiry(),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(session)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.login.success",
        actor=user.email,
        actor_type="user",
        result="success",
    )
    return {"token": token, "expires_at": session.expires_at.isoformat(), "user": {"email": user.email, "display_name": user.display_name}}


@router.post("/logout")
def logout(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: str | None = Header(default=None),
):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        db.query(SessionModel).filter(SessionModel.id == token).delete()
        db.commit()
    write_audit_event(db, event_category="auth", event_type="auth.logout", actor=user.email, actor_type="user")
    return {"status": "ok"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "display_name": user.display_name, "is_admin": user.is_admin}


@router.patch("/me")
def update_me(payload: MeUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Self-service profile edit -- any signed-in user (Viewer or Admin),
    not gated by require_admin, since this only ever touches the caller's
    own row. Changing the password requires the current one; there is no
    other path back in for a user who forgets it (no email-based reset --
    same constraint noted on the admin-only update_user endpoint above)."""
    changed_display_name = False
    changed_password = False

    if payload.display_name is not None:
        new_name = payload.display_name.strip()
        if not new_name:
            raise HTTPException(status_code=422, detail="Display name cannot be empty")
        if new_name != user.display_name:
            user.display_name = new_name
            changed_display_name = True

    if payload.new_password is not None:
        if not payload.current_password or not verify_password(payload.current_password, user.password_hash or ""):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        if len(payload.new_password) < 10:
            raise HTTPException(status_code=422, detail="Password must be at least 10 characters")
        user.password_hash = hash_password(payload.new_password)
        changed_password = True

    if changed_display_name or changed_password:
        write_audit_event(
            db,
            event_category="auth",
            event_type="auth.profile.updated",
            actor=user.email,
            actor_type="user",
            metadata={"changed_display_name": changed_display_name, "changed_password": changed_password},
        )
        db.commit()
        db.refresh(user)

    return {"email": user.email, "display_name": user.display_name, "is_admin": user.is_admin}


def _send_invite_email(db: Session, user: User, base_url: str | None, *, actor: str) -> tuple[bool, str | None]:
    """Best-effort: emails the invite link if SMTP is enabled/configured
    and the caller supplied a base URL. Never raises -- the invite itself
    (and its copy-paste link) already succeeded regardless of whether this
    works, matching the settings.test-email endpoint's own
    try/except-into-a-status-dict shape rather than failing the request."""
    if not base_url:
        return False, None
    settings = db.query(AppSettings).filter(AppSettings.id == 1).one()
    if not settings.smtp_enabled:
        return False, None

    link = f"{base_url}/accept-invite?token={user.invite_token}"
    try:
        send_email(
            settings,
            user.email,
            subject="You've been invited to PyXie",
            body=f"You've been invited to PyXie Manager. Set up your account:\n\n{link}\n\nThis link expires in {INVITE_TTL_DAYS} days and works once.",
        )
    except Exception as e:
        write_audit_event(
            db,
            event_category="auth",
            event_type="auth.invite.emailed",
            actor=actor,
            actor_type="user",
            result="failure",
            severity="warning",
            error=str(e),
            metadata={"invited_email": user.email},
        )
        return False, str(e)

    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.invite.emailed",
        actor=actor,
        actor_type="user",
        result="success",
        metadata={"invited_email": user.email},
    )
    return True, None


@router.post("/invite")
def invite_user(payload: InviteRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A user with that email already exists")

    user = User(
        email=payload.email,
        display_name=payload.display_name or payload.email.split("@")[0],
        password_hash=None,
        is_admin=payload.is_admin,
        is_active=True,
        invite_token=generate_session_token(),
        invite_token_expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(user)
    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.user.invited",
        actor=admin.email,
        actor_type="user",
        metadata={"invited_email": payload.email, "is_admin": payload.is_admin},
    )
    db.commit()
    db.refresh(user)
    email_sent, email_error = _send_invite_email(db, user, payload.invite_base_url, actor=admin.email)
    db.commit()
    return {
        "invite_token": user.invite_token,
        "expires_at": user.invite_token_expires_at.isoformat(),
        "email_sent": email_sent,
        "email_error": email_error,
        **_user_out(user),
    }


@router.get("/invite/{token}")
def get_invite(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.invite_token == token).one_or_none()
    if user is None or user.invite_token_expires_at is None or user.invite_token_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="Invite not found or expired")
    return {"email": user.email, "display_name": user.display_name}


@router.post("/accept-invite")
def accept_invite(payload: AcceptInviteRequest, db: Session = Depends(get_db)):
    if len(payload.password) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters")

    user = db.query(User).filter(User.invite_token == payload.token).one_or_none()
    if user is None or user.invite_token_expires_at is None or user.invite_token_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    user.password_hash = hash_password(payload.password)
    user.invite_token = None
    user.invite_token_expires_at = None
    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.invite.accepted",
        actor=user.email,
        actor_type="user",
        result="success",
    )
    db.commit()
    return {"status": "ok"}


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.email).all()
    return [_user_out(u) for u in users]


@router.patch("/users/{user_id}")
def update_user(
    user_id: str, payload: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Toggle is_admin/is_active. Refuses to let the last active admin
    demote or deactivate themselves -- there's no other way back in once
    that happens (no email-based password reset either)."""
    target = db.query(User).filter(User.id == user_id).one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    self_demoting = str(target.id) == str(admin.id) and (
        (payload.is_admin is False and target.is_admin) or (payload.is_active is False and target.is_active)
    )
    if self_demoting:
        other_active_admins = (
            db.query(User)
            .filter(User.id != target.id, User.is_admin.is_(True), User.is_active.is_(True))
            .count()
        )
        if other_active_admins == 0:
            raise HTTPException(status_code=400, detail="You are the only active admin -- promote another user first")

    before = {"is_admin": target.is_admin, "is_active": target.is_active}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.user.updated",
        actor=admin.email,
        actor_type="user",
        state_before=before,
        state_after={"is_admin": target.is_admin, "is_active": target.is_active},
        metadata={"target_email": target.email},
    )
    try:
        db.commit()
    except DBAPIError:
        # Backstop: the enforce_min_one_admin trigger (migration 54e735af2025)
        # rejected this at the database level -- the app-level check above
        # covers the common case, this only fires on the narrow TOCTOU race
        # between two concurrent requests, or a future code path that
        # forgot to re-check.
        db.rollback()
        raise HTTPException(status_code=400, detail="You are the only active admin -- promote another user first")
    db.refresh(target)
    return _user_out(target)


@router.delete("/users/{user_id}")
def delete_user(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Hard-deletes a user row -- the one deliberate exception to this
    app's otherwise-universal "nothing hard-deleted" pattern (sites,
    nodes, workloads, etc. all soft-delete via is_missing). Phil, on
    building out real offboarding: deactivate first (blocks login,
    already protected by the min-one-admin trigger/check), delete only
    once it's already deactivated -- so this endpoint refuses outright on
    a still-active account rather than repeating that protection itself."""
    target = db.query(User).filter(User.id == user_id).one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_active:
        raise HTTPException(status_code=400, detail="Deactivate this user before deleting them")

    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.user.deleted",
        actor=admin.email,
        actor_type="user",
        metadata={"target_email": target.email, "was_admin": target.is_admin},
    )
    db.delete(target)
    db.commit()
    return {"status": "ok"}


@router.post("/users/{user_id}/reinvite")
def reinvite_user(
    user_id: str, payload: ReinviteRequest = ReinviteRequest(), admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    target = db.query(User).filter(User.id == user_id).one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.password_hash is not None:
        raise HTTPException(status_code=400, detail="This user has already accepted their invite")

    target.invite_token = generate_session_token()
    target.invite_token_expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    write_audit_event(
        db,
        event_category="auth",
        event_type="auth.user.reinvited",
        actor=admin.email,
        actor_type="user",
        metadata={"target_email": target.email},
    )
    db.commit()
    db.refresh(target)
    email_sent, email_error = _send_invite_email(db, target, payload.invite_base_url, actor=admin.email)
    db.commit()
    return {
        "invite_token": target.invite_token,
        "expires_at": target.invite_token_expires_at.isoformat(),
        "email_sent": email_sent,
        "email_error": email_error,
    }
