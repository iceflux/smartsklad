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
        "notifications.send", "notifications.view",
    ],
}


class UserConflictError(ValueError):
    pass


class UserPermissionError(ValueError):
    pass


class RoleNotFoundError(ValueError):
    pass


USERNAME_MAX_LEN = 64


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
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return user


def login(db: Session, username: str, password: str, ip: str | None = None) -> AuthSession | None:
    user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return create_session(db, user, ip)


def logout(db: Session, token: str) -> None:
    session = db.query(AuthSession).filter(AuthSession.token == token).first()
    if session:
        user_id = session.user_id
        entity_id = session.id
        db.delete(session)
        db.commit()
        log_action(db, action="logout", entity_type="session", entity_id=entity_id, user_id=user_id)


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


def generate_password(charset: str, length: int) -> str:
    return "".join(secrets.choice(charset) for _ in range(length))


def create_user(db: Session, *, username: str, email: str | None, password: str,
                role_id: int, actor: User, is_active: bool = True) -> User:
    username = username.strip()
    if not username:
        raise ValueError("Логин не может быть пустым")
    if len(username) > USERNAME_MAX_LEN:
        raise ValueError("Логин не длиннее 64 символов")
    if db.query(User).filter(User.username == username).first():
        raise UserConflictError("Логин уже занят")
    email = (email or "").strip() or None
    if email is not None and db.query(User).filter(User.email == email).first():
        raise UserConflictError("Email уже занят")
    if len(password) < 8:
        raise ValueError("Пароль должен быть не короче 8 символов")
    role = db.get(Role, role_id)
    if role is None:
        raise RoleNotFoundError("Роль не найдена")
    if not actor.role.is_super and role.is_super:
        raise UserPermissionError("Только администратор может назначать супер-роль")
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role_id=role.id,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_action(db, action="user.create", entity_type="user", entity_id=user.id,
               new_value=user.username, user_id=actor.id)
    return user


def _active_super_ids_query(db: Session):
    return (
        db.query(User.id)
        .join(Role, User.role_id == Role.id)
        .filter(User.is_active.is_(True), Role.is_super.is_(True))
        .with_for_update()
    )


def update_user(db: Session, user: User, *, actor: User, username: str | None = None,
                email: str | None = None, password: str | None = None,
                role_id: int | None = None, is_active: bool | None = None) -> User:
    if actor.id == user.id and role_id is not None and role_id != user.role_id and not actor.role.is_super:
        raise UserPermissionError("Нельзя менять собственную роль")
    if actor.id == user.id and is_active is False:
        raise UserPermissionError("Нельзя деактивировать собственную учётную запись")
    if not actor.role.is_super and user.role and user.role.is_super and (role_id is not None or username is not None or email is not None or password is not None or is_active is not None):
        raise UserPermissionError("Только администратор может изменять супер-пользователя")
    if is_active is False and user.role and user.role.is_super and user.is_active:
        if len(_active_super_ids_query(db).all()) <= 1:
            raise UserPermissionError("Нельзя деактивировать последнего администратора")

    changed = []
    pending = {}
    if username is not None and username.strip() != user.username:
        new_username = username.strip()
        if not new_username:
            raise ValueError("Логин не может быть пустым")
        if len(new_username) > USERNAME_MAX_LEN:
            raise ValueError("Логин не длиннее 64 символов")
        if db.query(User).filter(User.username == new_username, User.id != user.id).first():
            raise UserConflictError("Логин уже занят")
        changed.append(("Логин", user.username, new_username))
        pending["username"] = new_username
    if email is not None:
        new_email = email.strip() or None
        if new_email != user.email:
            if new_email is not None and db.query(User).filter(User.email == new_email, User.id != user.id).first():
                raise UserConflictError("Email уже занят")
            changed.append(("Email", user.email, new_email))
            pending["email"] = new_email
    if password is not None:
        if len(password) < 8:
            raise ValueError("Пароль должен быть не короче 8 символов")
        changed.append(("Пароль", "***", "***"))
        pending["password_hash"] = hash_password(password)
        pending["reset_token"] = None
        pending["reset_token_expires_at"] = None
    if role_id is not None and role_id != user.role_id:
        new_role = db.get(Role, role_id)
        if new_role is None:
            raise RoleNotFoundError("Роль не найдена")
        if not actor.role.is_super and new_role.is_super:
            raise UserPermissionError("Только администратор может назначать супер-роль")
        changed.append(("Роль", user.role_name, new_role.name))
        pending["role_id"] = new_role.id
    if is_active is not None and is_active != user.is_active:
        changed.append(("Активен", "да" if user.is_active else "нет", "да" if is_active else "нет"))
        pending["is_active"] = is_active

    for field, value in pending.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    for label, old, new in changed:
        action = "user.role.change" if label == "Роль" else ("user.status_change" if label == "Активен" else "user.update")
        log_action(db, action=action, entity_type="user", entity_id=user.id,
                   old_value=old, new_value=new, user_id=actor.id)
    return user