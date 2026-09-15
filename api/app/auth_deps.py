from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session as DbSession

from pyxie_core.models import Session as SessionModel, User

from .deps import get_db


def get_current_user(
    authorization: str | None = Header(default=None),
    db: DbSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()

    session = db.query(SessionModel).filter(SessionModel.id == token).one_or_none()
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    if session.expires_at < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired")

    user = db.query(User).filter(User.id == session.user_id, User.is_active.is_(True)).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate for every mutating endpoint in the app. Viewer accounts
    (is_admin=False) get a clean 403 here -- this is the actual security
    boundary; any UI-level hiding of write buttons is just polish on top
    of this, not a substitute for it."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
