import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.audit.service import log_action
from app.modules.auth.models import AuthSession, Role, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_admin_if_empty(db: Session) -> bool:
    if not (settings.admin_username and settings.admin_password):
        return False
    if db.query(User).first() is not None:
        return False
    user = User(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        role=Role.admin,
    )
    db.add(user)
    db.commit()
    return True


def create_session(db: Session, user: User, ip: str | None = None) -> AuthSession:
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days),
    )
    db.add(session)
    db.commit()
    log_action(db, action="login", entity_type="user", entity_id=user.id, user_id=user.id, ip=ip)
    return session


def get_user_by_token(db: Session, token: str) -> User | None:
    session = db.query(AuthSession).filter(AuthSession.token == token).first()
    if session is None:
        return None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    return db.get(User, session.user_id)


def login(db: Session, username: str, password: str, ip: str | None = None) -> AuthSession | None:
    user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return create_session(db, user, ip)


def logout(db: Session, token: str) -> None:
    session = db.query(AuthSession).filter(AuthSession.token == token).first()
    if session:
        db.delete(session)
        db.commit()