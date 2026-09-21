import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.audit.service import log_action
from app.modules.auth.models import AuthSession, Role, User
from app.modules.notifications.email import send_email

DEFAULT_PERMISSIONS = {
    "admin": [],
    "seller": ["products.view", "orders.view", "orders.create", "orders.edit", "orders.status", "customers.manage"],
    "warehouseman": ["products.view", "stock.change", "orders.view", "orders.status"],
    "manager": [
        "products.view", "price.change",
        "orders.view", "orders.create", "orders.edit", "orders.delete", "orders.status",
        "customers.manage", "analytics.view", "reports.export",
    ],
}


def ensure_default_roles(db: Session) -> dict[str, Role]:
    roles = {}
    for name, perms in DEFAULT_PERMISSIONS.items():
        role = db.query(Role).filter(Role.name == name).first()
        if role is None:
            role = Role(
                name=name,
                is_system=True,
                is_super=(name == "admin"),
                permissions=perms,
            )
            db.add(role)
            db.commit()
        roles[name] = role
    return roles


def get_role_by_name(db: Session, name: str) -> Role | None:
    return db.query(Role).filter(Role.name == name).first()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_admin_if_empty(db: Session) -> bool:
    if not (settings.admin_username and settings.admin_password):
        return False
    if db.query(User).first() is not None:
        return False
    roles = ensure_default_roles(db)
    user = User(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        role_id=roles["admin"].id,
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


def request_password_reset(db: Session, username_or_email: str) -> str | None:
    user = (
        db.query(User)
        .filter((User.username == username_or_email) | (User.email == username_or_email))
        .first()
    )
    if user is None or user.email is None:
        return None

    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()

    link = f"{settings.base_url}/reset-password?token={token}"
    send_email(
        user.email,
        "Восстановление пароля — СмартСклад",
        f"Для сброса пароля перейдите по ссылке: {link}",
    )
    return user.email


def reset_password(db: Session, token: str, new_password: str) -> bool:
    user = (
        db.query(User)
        .filter(User.reset_token == token, User.reset_token_expires_at.isnot(None))
        .first()
    )
    if user is None:
        return False
    expires_at = user.reset_token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return False
    user.password_hash = hash_password(new_password)
    user.reset_token = None
    user.reset_token_expires_at = None
    db.commit()
    log_action(db, action="password_reset", entity_type="user", entity_id=user.id, user_id=user.id)
    return True


def admin_reset_password(db: Session, admin: User, target: User) -> str:
    temp = secrets.token_urlsafe(12)
    target.password_hash = hash_password(temp)
    target.reset_token = None
    target.reset_token_expires_at = None
    db.commit()
    log_action(db, action="admin_password_reset", entity_type="user", entity_id=target.id, user_id=admin.id)
    return temp