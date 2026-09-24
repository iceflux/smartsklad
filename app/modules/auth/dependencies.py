from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.auth.models import User
from app.modules.auth.permissions import Permission
from app.modules.auth.service import get_user_by_token

SESSION_COOKIE = "session"


def get_current_user(
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> User:
    if session_cookie is None:
        raise HTTPException(status_code=401, detail="Не авторизован")
    user = get_user_by_token(db, session_cookie)
    if user is None:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return user


def user_has_permission(user: User, perm: Permission) -> bool:
    return user.role.is_super or perm.value in (user.role.permissions or [])


def ensure_permissions(user: User, *perms: Permission) -> None:
    if not all(user_has_permission(user, p) for p in perms):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def require_permissions(*perms: Permission):
    def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        ensure_permissions(user, *perms)
        return user

    return checker


def get_page_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return get_user_by_token(db, token)


def page_context(user: User) -> dict[str, bool]:
    return {
        "can_view_products": user_has_permission(user, Permission.products_view),
        "can_view_categories": user_has_permission(user, Permission.products_view),
        "can_view_roles": user_has_permission(user, Permission.users_manage),
        "can_view_users": user_has_permission(user, Permission.users_manage),
        "can_view_audit": user_has_permission(user, Permission.audit_view),
        "can_view_customers": user_has_permission(user, Permission.customers_manage),
        "can_view_orders": user_has_permission(user, Permission.orders_view),
        "can_change_settings": user_has_permission(user, Permission.settings_change),
        "can_send_notifications": user_has_permission(user, Permission.notifications_send),
        "can_view_notifications": user_has_permission(user, Permission.notifications_view),
        "can_view_analytics": user_has_permission(user, Permission.analytics_view),
        "can_manage_backups": user_has_permission(user, Permission.backup_manage),
    }