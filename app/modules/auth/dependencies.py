from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.auth.models import User
from app.modules.auth.permissions import Permission
from app.modules.auth.service import get_user_by_token
from app.modules.settings.service import (
    DEFAULT_SEARCH_SLOW_MS,
    DEFAULT_THEME,
    search_slow_ms,
    sidebar_collapsed,
    user_theme,
)

SESSION_COOKIE = "session"

NAV_GROUPS = (
    ("Продажи", (("Клиенты", "/customers", "can_view_customers"),)),
    ("Склад", (
        ("Товары", "/products", "can_view_products"),
        ("Категории", "/categories", "can_view_categories"),
        ("Единицы измерения", "/units", "can_manage_units"),
    )),
    ("Анализ", (
        ("Аналитика", "/dashboard", "can_view_analytics"),
        ("Прогноз", "/forecast", "can_view_analytics"),
    )),
    ("Система", (
        ("Импорт", "/import", "can_manage_import"),
        ("Резервные копии", "/backups", "can_manage_backups"),
        ("Журнал аудита", "/audit", "can_view_audit"),
        ("Роли", "/roles", "can_view_roles"),
        ("Пользователи", "/users", "can_view_roles"),
        ("Настройки", "/settings", "can_change_settings"),
        ("Уведомления", "/notifications", "can_view_notifications"),
        ("Справка", "/help", "can_view_help"),
    )),
)


def sidebar_nav(flags: dict[str, bool]) -> list[tuple[str, list[tuple[str, str]]]]:
    return [
        (title, [(label, href) for label, href, flag in items if flags.get(flag)])
        for title, items in NAV_GROUPS
    ]


def nav_is_current(path: str, href: str) -> bool:
    if href == "/":
        return path == "/"
    return path == href or path.startswith(href + "/")


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
    request.state.sidebar_collapsed = False
    request.state.theme = DEFAULT_THEME
    request.state.search_slow_ms = DEFAULT_SEARCH_SLOW_MS
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    user = get_user_by_token(db, token)
    if user is None:
        return None
    request.state.sidebar_collapsed = sidebar_collapsed(db, user.id)
    request.state.theme = user_theme(db, user.id)
    request.state.search_slow_ms = search_slow_ms(db)
    return user


def page_context(user: User) -> dict[str, bool]:
    flags = {
        "can_view_products": user_has_permission(user, Permission.products_view),
        "can_view_categories": user_has_permission(user, Permission.products_view),
        "can_view_roles": user_has_permission(user, Permission.users_manage),
        "can_view_users": user_has_permission(user, Permission.users_manage),
        "can_view_audit": user_has_permission(user, Permission.audit_view),
        "can_view_customers": user_has_permission(user, Permission.customers_manage),
        "can_view_orders": user_has_permission(user, Permission.orders_view),
        "can_create_orders": user_has_permission(user, Permission.orders_create),
        "can_change_settings": user_has_permission(user, Permission.settings_change),
        "can_send_notifications": user_has_permission(user, Permission.notifications_send),
        "can_view_notifications": user_has_permission(user, Permission.notifications_view),
        "can_view_analytics": user_has_permission(user, Permission.analytics_view),
        "can_manage_backups": user_has_permission(user, Permission.backup_manage),
        "can_manage_import": user_has_permission(user, Permission.import_manage),
        "can_manage_units": user_has_permission(user, Permission.units_manage),
        "can_view_help": True,
    }
    return {**flags, "nav": sidebar_nav(flags)}