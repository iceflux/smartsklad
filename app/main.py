import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, time
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.modules.audit.router import router as audit_router
from app.modules.audit.models import AuditLog
from app.modules.ai.router import router as ai_router
from app.modules.ai.service import forecast_rows
from app.modules.dashboard.router import router as dashboard_router
from app.modules.dashboard.service import (
    DEFAULT_PRESET,
    chart_bars,
    dashboard_metrics,
    dashboard_sales_csv,
    resolve_period,
)
from app.modules.auth.dependencies import (
    SESSION_COOKIE, ensure_permissions, get_page_user as _page_user,
    page_context as _page_context, user_has_permission,
)
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.auth.roles_router import router as roles_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import create_admin_if_empty, generate_password, logout
from app.modules.auth.users_router import router as users_router
import app.modules.inventory.models  # noqa: F401
import app.modules.customers.models  # noqa: F401
import app.modules.orders.models  # noqa: F401
import app.modules.backup.models  # noqa: F401
from app.modules.backup.maintenance import (
    MAX_COUNTDOWN_SECONDS,
    MIN_COUNTDOWN_SECONDS,
    arm_restore,
    maintenance_middleware,
    maintenance_state,
)
from app.modules.backup.models import BackupLog
from app.modules.backup.report import read_report_settings, send_report, write_report_settings
from app.modules.backup.router import router as backup_router
from app.modules.backup.service import (
    create_backup,
    get_backup_settings,
    list_archives,
    parse_archive_name,
    pg_dump_health,
    update_backup_settings,
)
from app.modules.backup.scheduler import scheduler_loop
from app.modules.inventory.models import Category, PriceHistory
from app.modules.customers.models import Customer
from app.modules.customers.router import router as customers_router
from app.modules.inventory.router import router as inventory_router
from app.modules.orders.router import router as orders_router
from app.modules.inventory.service import (
    change_bulk_category,
    delete_product,
    export_products_csv,
    get_category_tree,
    get_product,
    list_products,
    restore_product,
    update_product,
)
from app.modules.customers.service import (
    delete_customer,
    get_customer,
    list_customers,
    restore_customer,
)
from app.modules.orders.service import (
    NEXT_POSSIBLE,
    VALID_STATUSES,
    change_status,
    copy_order,
    create_order,
    customer_order_stats,
    delete_order,
    get_order,
    list_orders,
    restore_order,
    update_order,
)
from app.modules.settings.router import router as settings_router
from app.modules.notifications.router import router as notifications_router
from app.modules.notifications.models import NotificationLog
from app.modules.settings.service import (
    DEFAULT_MIN_STOCK_DAYS,
    DEFAULT_PASSWORD_CHARSET,
    DEFAULT_PASSWORD_LENGTH,
    MIN_STOCK_DAYS_KEY,
    STOCK_TRACK_DEFAULT_KEY,
    USER_PASSWORD_CHARSET_KEY,
    USER_PASSWORD_LENGTH_KEY,
    ensure_default_settings,
    get_int_setting,
    get_setting,
    set_setting,
)
from app.modules.printing.service import render_order_pdf

APP_DIR = Path(__file__).parent
PAGE_SIZE = 25

STATUS_LABELS = {
    "new": "Новый",
    "processing": "В обработке",
    "done": "Выполнен",
    "cancelled": "Отменён",
}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _client_options(db: Session, include_deleted: bool = False) -> list[dict]:
    items, _ = list_customers(db, include_deleted=include_deleted)
    return [{"id": c.id, "name": c.name} for c in items]


def _product_options(db: Session) -> list[dict]:
    items, _ = list_products(db, include_deleted=False)
    return [{"id": p.id, "name": p.name, "price": p.price, "rest": p.rest} for p in items]


def _order_form_items(form: dict) -> list[dict]:
    items = []
    for pid, qty, disc in zip(
        form.get("product_id", []),
        form.get("quantity", []),
        form.get("line_discount", []),
    ):
        if not pid or not pid.isdigit():
            continue
        try:
            items.append({
                "product_id": int(pid),
                "quantity": int(qty or 0),
                "discount": float(disc or 0),
            })
        except ValueError:
            continue
    return items


def _to_int(raw, default):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _page_param(raw):
    return min(10000, max(1, _to_int(raw, 1)))


def _category_label(category: Category | None) -> str:
    if category is None:
        return ""
    if category.parent is not None:
        return f"{category.parent.name} → {category.name}"
    return category.name


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not app.state.skip_seed_admin:
            create_admin_if_empty(SessionLocal())
            ensure_default_settings(SessionLocal())  # seed глобалки дефолт; в тестах skip_seed_admin=True → не триггерим реальный PG
        task = None
        if not app.state.skip_seed_admin:
            task = asyncio.create_task(scheduler_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="СмартСклад", lifespan=lifespan)
    app.state.skip_seed_admin = False
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.middleware("http")(maintenance_middleware)
    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    templates.env.globals["maintenance_state"] = maintenance_state
    app.state.templates = templates

    app.include_router(auth_router)
    app.include_router(roles_router, prefix="/api")
    app.include_router(users_router, prefix="/api")
    app.include_router(inventory_router, prefix="/api")
    app.include_router(customers_router, prefix="/api")
    app.include_router(orders_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(notifications_router, prefix="/api")
    app.include_router(ai_router, prefix="/api")
    app.include_router(dashboard_router, prefix="/api")
    app.include_router(backup_router, prefix="/api")
    app.include_router(audit_router)

    @app.get("/")
    def index(request: Request, user: Annotated[User | None, Depends(_page_user)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request, "index.html", {"title": "СмартСклад", "user": user, **_page_context(user)}
        )

    @app.get("/login")
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"title": "Вход"})

    @app.get("/reset-password")
    def reset_password_page(request: Request):
        token = request.query_params.get("token", "")
        return templates.TemplateResponse(
            request, "reset_password.html", {"title": "Восстановление пароля", "token": token}
        )

    @app.post("/logout")
    def page_logout(
        session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if session_cookie:
            logout(db, session_cookie)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/settings")
    def settings_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.settings_change)
        token = get_setting(db, "telegram_bot_token") or ""
        chat_id = get_setting(db, "telegram_chat_id") or ""
        return templates.TemplateResponse(request, "settings.html", {
            "title": "Настройки", "user": user,
            "stock_track_default": get_setting(db, STOCK_TRACK_DEFAULT_KEY),
            "telegram_token_mask": (token[:3] + "****" + token[-3:]) if len(token) > 6 else ("****" if token else ""),
            "telegram_configured": bool(token and chat_id),
            "telegram_chat_id": chat_id,
            "msg": request.query_params.get("msg"),
            "user_password_charset": get_setting(db, USER_PASSWORD_CHARSET_KEY) or DEFAULT_PASSWORD_CHARSET,
            "user_password_length": get_setting(db, USER_PASSWORD_LENGTH_KEY) or DEFAULT_PASSWORD_LENGTH,
            "min_stock_days": get_int_setting(db, MIN_STOCK_DAYS_KEY, DEFAULT_MIN_STOCK_DAYS),
            **_page_context(user),
        })

    @app.post("/settings")
    async def settings_page_post(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.settings_change)
        form = parse_qs((await request.body()).decode())
        track_rest_default = (form.get("track_rest_default") or ["true"])[0]
        track_rest_default = "true" if track_rest_default == "on" else "false"
        telegram_token = (form.get("telegram_bot_token") or [""])[0].strip()
        telegram_chat = (form.get("telegram_chat_id") or [""])[0].strip()
        charset = (form.get("user_password_charset") or [""])[0].strip()
        length_raw = (form.get("user_password_length") or [""])[0].strip()
        try:
            length = int(length_raw)
        except ValueError:
            length = 0
        min_stock_days_raw = (form.get("min_stock_days") or [""])[0].strip()
        try:
            min_stock_days = int(min_stock_days_raw)
        except ValueError:
            min_stock_days = -1
        if not charset or not 8 <= length <= 64 or not 0 <= min_stock_days <= 365:
            return RedirectResponse("/settings?msg=Ошибка: символы непустые, длина 8-64, запас 0-365", status_code=303)
        set_setting(db, STOCK_TRACK_DEFAULT_KEY, track_rest_default, user=user)
        if telegram_token:
            set_setting(db, "telegram_bot_token", telegram_token, user=user)
        if telegram_chat:
            set_setting(db, "telegram_chat_id", telegram_chat, user=user)
        set_setting(db, USER_PASSWORD_CHARSET_KEY, charset, user=user)
        set_setting(db, USER_PASSWORD_LENGTH_KEY, str(length), user=user)
        set_setting(db, MIN_STOCK_DAYS_KEY, str(min_stock_days), user=user)
        return RedirectResponse("/settings?msg=Сохранено", status_code=303)

    @app.get("/notifications")
    def notifications_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None,
                           db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.notifications_view)
        channel = request.query_params.get("channel") or None
        status = request.query_params.get("status") or None
        raw_page = request.query_params.get("page") or "1"
        page = _page_param(raw_page)
        q = db.query(NotificationLog)
        if channel:
            q = q.filter(NotificationLog.channel == channel)
        if status:
            q = q.filter(NotificationLog.status == status)
        total = q.count()
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        rows = q.order_by(NotificationLog.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
        usernames = {u.id: u.username for u in db.query(User).order_by(User.id).all()}
        logs = [
            {
                "channel": r.channel, "recipient": r.recipient, "status": r.status,
                "error": r.error or "", "document_id": r.document_id,
                "username": usernames.get(r.user_id) or "—",
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            }
            for r in rows
        ]
        base_qs = urlencode({k: v for k, v in {
            "channel": channel, "status": status,
        }.items() if v not in (None, "")})
        return templates.TemplateResponse(request, "notifications.html", {
            "title": "Уведомления", "user": user, "logs": logs, "total": total,
            "page": page, "pages": pages, "base_qs": base_qs,
            "channel": channel or "", "status": status or "",
            "msg": request.query_params.get("msg"),
            **_page_context(user),
        })

    @app.get("/roles")
    def roles_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.users_manage)
        rows = db.query(Role).order_by(Role.id).all()
        counts = dict(db.query(User.role_id, func.count(User.id)).group_by(User.role_id).all())
        roles = [
            {
                "id": r.id, "name": r.name, "is_system": r.is_system, "is_super": r.is_super,
                "permissions": r.permissions or [], "user_count": counts.get(r.id, 0),
            }
            for r in rows
        ]
        return templates.TemplateResponse(request, "roles.html", {"title": "Роли", "user": user, "roles": roles, **_page_context(user)})

    @app.get("/roles/new")
    def roles_new_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.users_manage)
        return templates.TemplateResponse(
            request, "roles_edit.html",
            {"title": "Создание роли", "user": user, "role": None, "is_new": True, "groups": _perms_groups([]), **_page_context(user)},
        )

    @app.get("/roles/{role_id}/edit")
    def roles_edit_page(role_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.users_manage)
        role = db.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Роль не найдена")
        return templates.TemplateResponse(
            request, "roles_edit.html",
            {"title": f"Роль {role.name}", "user": user, "role": role, "is_new": False, "groups": _perms_groups(role.permissions or []), **_page_context(user)},
        )

    @app.get("/users")
    def users_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.users_manage)
        users = db.query(User).order_by(User.id).all()
        roles = [{"id": r.id, "name": r.name} for r in db.query(Role).order_by(Role.id).all()]
        users_view = [
            {
                "id": u.id, "username": u.username, "email": u.email, "is_active": u.is_active,
                "role_id": u.role_id, "role_name": u.role_name,
                "can_change_role": not (u.id == user.id and not user.role.is_super),
            }
            for u in users
        ]
        return templates.TemplateResponse(
            request, "users.html",
            {"title": "Пользователи", "user": user, "users": users_view, "roles": roles,
             "current_user": user, "is_super": user.role.is_super,
             "msg": request.query_params.get("msg"), **_page_context(user)},
        )

    @app.get("/users/new")
    def users_new_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.users_manage)
        charset = get_setting(db, USER_PASSWORD_CHARSET_KEY) or DEFAULT_PASSWORD_CHARSET
        length_raw = get_setting(db, USER_PASSWORD_LENGTH_KEY) or DEFAULT_PASSWORD_LENGTH
        try:
            length = int(length_raw)
        except ValueError:
            length = DEFAULT_PASSWORD_LENGTH
        roles = [{"id": r.id, "name": r.name} for r in db.query(Role).order_by(Role.id).all()]
        return templates.TemplateResponse(request, "user_form.html", {
            "title": "Создание пользователя", "user": user, "user_": None, "is_new": True,
            "roles": roles, "generated_password": generate_password(charset, length),
            "msg": request.query_params.get("msg"), "current_user": user, **_page_context(user),
        })

    @app.get("/users/{user_id}/edit")
    def users_edit_page(user_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.users_manage)
        target = db.get(User, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        roles = [{"id": r.id, "name": r.name} for r in db.query(Role).order_by(Role.id).all()]
        return templates.TemplateResponse(request, "user_form.html", {
            "title": "Редактирование пользователя", "user": user, "user_": target, "is_new": False,
            "roles": roles, "generated_password": None, "msg": request.query_params.get("msg"),
            "current_user": user,
            "can_manage_role": user.role.is_super or not (target.role and target.role.is_super),
            "can_toggle_active": user.role.is_super or not (target.role and target.role.is_super),
            **_page_context(user),
        })

    @app.get("/products")
    def products_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_view)
        search = request.query_params.get("search") or None
        raw_category = request.query_params.get("category_id")
        category_id = _to_int(raw_category, None)
        include_deleted = request.query_params.get("include_deleted") == "1"
        raw_page = request.query_params.get("page") or "1"
        page = _page_param(raw_page)
        items, total = list_products(db, search=search, category_id=category_id,
                                     include_deleted=include_deleted, page=page)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        products = [
            {
                "id": p.id, "name": p.name, "sku": p.sku,
                "category_name": _category_label(p.category),
                "unit": p.unit, "price": p.price, "rest": p.rest,
                "is_deleted": p.is_deleted,
            }
            for p in items
        ]
        qs = urlencode({k: v for k, v in {
            "search": search, "category_id": category_id,
            "include_deleted": "1" if include_deleted else None,
        }.items() if v not in (None, "")})
        return templates.TemplateResponse(request, "products.html", {
            "title": "Товары", "user": user, "products": products, "total": total,
            "page": page, "pages": pages, "base_qs": qs, "msg": request.query_params.get("msg"),
            "search": search or "", "category_id": category_id, "include_deleted": include_deleted,
            "tree": get_category_tree(db),
            "can_edit": user_has_permission(user, Permission.products_edit),
            "can_stock": user_has_permission(user, Permission.stock_change),
            "can_price": user_has_permission(user, Permission.price_change),
            "can_export": user_has_permission(user, Permission.reports_export),
            **_page_context(user),
        })

    @app.get("/products/new")
    def products_new_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_edit)
        return templates.TemplateResponse(request, "product_form.html", {
            "title": "Новый товар", "user": user, "product": None, "is_new": True,
            "tree": get_category_tree(db), "characteristic_json": "",
            "can_edit": True, "can_price": True,
            "can_stock": user_has_permission(user, Permission.stock_change),
            "stock_track_default": get_setting(db, STOCK_TRACK_DEFAULT_KEY),
            "min_stock_days_hint": get_int_setting(db, MIN_STOCK_DAYS_KEY, DEFAULT_MIN_STOCK_DAYS),
            **_page_context(user),
        })

    @app.get("/products/export.csv")
    def products_export_page(user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.reports_export)
        return Response(
            content=export_products_csv(db),
            media_type="application/csv",
            headers={"Content-Disposition": 'attachment; filename="products.csv"'},
        )

    @app.get("/dashboard/export.csv")
    def dashboard_export_page(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.reports_export)
        try:
            start, end = resolve_period(
                request.query_params.get("preset"),
                request.query_params.get("date_from"),
                request.query_params.get("date_to"),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        daily = dashboard_metrics(db, start, end)["daily"]
        return Response(
            content=dashboard_sales_csv(daily),
            media_type="application/csv",
            headers={"Content-Disposition": 'attachment; filename="dashboard.csv"'},
        )

    @app.get("/dashboard")
    def dashboard_page(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.analytics_view)
        preset = request.query_params.get("preset") or DEFAULT_PRESET
        try:
            start, end = resolve_period(
                preset,
                request.query_params.get("date_from"),
                request.query_params.get("date_to"),
            )
        except ValueError as e:
            return RedirectResponse(
                "/dashboard?" + urlencode({"preset": preset, "msg": str(e)}), status_code=303
            )
        metrics = dashboard_metrics(db, start, end)
        return templates.TemplateResponse(request, "dashboard.html", {
            "title": "Аналитика продаж", "user": user,
            "metrics": metrics, "chart": chart_bars(metrics["daily"]),
            "preset": preset,
            "can_export": user_has_permission(user, Permission.reports_export),
            "msg": request.query_params.get("msg"),
            **_page_context(user),
        })

    @app.get("/backups")
    def backups_page(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.backup_manage)
        row = get_backup_settings(db)
        candidates = (
            db.query(User)
            .filter(User.is_active.is_(True), User.email.isnot(None), User.email != "")
            .order_by(User.username)
            .all()
        )
        return templates.TemplateResponse(request, "backups.html", {
            "title": "Резервные копии", "user": user,
            "settings_row": row,
            "scheduler": {
                "last_check_at": row.last_check_at,
                "last_backup_at": row.last_backup_at,
                "last_status": row.last_status,
                "last_error": row.last_error,
                "last_report_at": row.last_report_at,
                "last_report_sent": row.last_report_sent,
                "last_report_skipped": row.last_report_skipped,
            },
            "health": pg_dump_health(db, row),
            "archives": list_archives(row),
            "log_entries": db.query(BackupLog).order_by(BackupLog.id.desc()).limit(20).all(),
            "report": read_report_settings(db),
            "candidates": candidates,
            "maintenance": maintenance_state(),
            "msg": request.query_params.get("msg"),
            **_page_context(user),
        })

    @app.post("/backups/run")
    def backups_run_post(
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.backup_manage)
        result = create_backup(db, user_id=user.id, kind="manual")
        key = "Копия создана" if result["status"] == "success" else f"Ошибка: {result['error']}"
        return RedirectResponse("/backups?" + urlencode({"msg": key}), status_code=303)

    @app.post("/backups/settings")
    async def backups_settings_post(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.backup_manage)
        form = parse_qs((await request.body()).decode())
        try:
            update_backup_settings(
                db,
                enabled="enabled" in form,
                schedule_time=(form.get("schedule_time") or [""])[0],
                check_interval_minutes=int((form.get("check_interval_minutes") or ["0"])[0]),
                retention_days=int((form.get("retention_days") or ["0"])[0]),
                local_path=(form.get("local_path") or [""])[0],
                pg_dump_path=(form.get("pg_dump_path") or [""])[0],
                user_id=user.id,
            )
            key = "Настройки сохранены"
        except ValueError as e:
            db.rollback()
            key = "Ошибка: " + str(e)
        return RedirectResponse("/backups?" + urlencode({"msg": key}), status_code=303)

    @app.post("/backups/report")
    async def backups_report_post(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.backup_manage)
        form = parse_qs((await request.body()).decode())
        recipient_ids = [int(v) for v in form.get("recipient_ids", []) if v.isdigit()]
        try:
            write_report_settings(
                db, enabled="enabled" in form,
                period=(form.get("period") or ["week"])[0],
                send_time=(form.get("send_time") or ["09:00"])[0],
                recipient_ids=recipient_ids, user_id=user.id,
            )
            key = "Настройки отчёта сохранены"
        except ValueError as e:
            db.rollback()
            key = "Ошибка: " + str(e)
        return RedirectResponse("/backups?" + urlencode({"msg": key}), status_code=303)

    @app.post("/backups/report/send")
    def backups_report_send_post(
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.backup_manage)
        result = send_report(db, user_id=user.id)
        key = (f"Отправлено: {result['sent']}, "
               f"пропущено: {result['skipped']}, ошибок: {result['failed']}")
        return RedirectResponse("/backups?" + urlencode({"msg": key}), status_code=303)

    @app.post("/backups/restore")
    async def backups_restore_post(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.backup_manage)
        form = parse_qs((await request.body()).decode())
        filename = (form.get("filename") or [""])[0]
        try:
            delay = int((form.get("delay") or ["60"])[0])
        except (TypeError, ValueError):
            delay = 60
        row = get_backup_settings(db)
        if parse_archive_name(filename) is None or not (Path(row.local_path) / filename).is_file():
            return RedirectResponse(
                "/backups?" + urlencode({"msg": "Ошибка: Архив не найден"}), status_code=303)
        if not MIN_COUNTDOWN_SECONDS <= delay <= MAX_COUNTDOWN_SECONDS:
            return RedirectResponse(
                "/backups?" + urlencode({"msg": "Ошибка: ожидание от 10 до 3600 секунд"}),
                status_code=303)
        asyncio.create_task(arm_restore(filename, delay, user_id=user.id))
        return RedirectResponse(
            "/backups?" + urlencode({"msg": f"Восстановление начнётся через {delay} с"}),
            status_code=303)

    @app.get("/backups/download/{filename}")
    def backups_download(
        filename: str,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.backup_manage)
        if parse_archive_name(filename) is None:
            raise HTTPException(status_code=404, detail="архив не найден")
        path = Path(get_backup_settings(db).local_path) / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="архив не найден")
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    @app.get("/forecast")
    def forecast_page(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.analytics_view)
        only_purchase = request.query_params.get("only_purchase") == "1"
        page = _page_param(request.query_params.get("page"))
        rows, total = forecast_rows(
            db,
            only_purchase=only_purchase,
            page=page,
            min_stock_days=get_int_setting(db, MIN_STOCK_DAYS_KEY, DEFAULT_MIN_STOCK_DAYS),
        )
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        base_qs = urlencode({"only_purchase": "1"} if only_purchase else {})
        return templates.TemplateResponse(request, "forecast.html", {
            "title": "Прогноз спроса", "user": user, "rows": rows, "total": total,
            "page": page, "pages": pages, "base_qs": base_qs, "only_purchase": only_purchase,
            "msg": request.query_params.get("msg"),
            **_page_context(user),
        })

    @app.get("/products/{product_id}")
    def products_detail_page(product_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_view)
        product = get_product(db, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        history = (
            db.query(PriceHistory)
            .filter(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.changed_at.desc())
            .all()
        )
        price_history = [
            {
                "old_price": h.old_price, "new_price": h.new_price,
                "changed_by": h.changer.username if h.changer else None,
                "changed_at": h.changed_at.strftime("%Y-%m-%d %H:%M") if h.changed_at else "",
            }
            for h in history
        ]
        return templates.TemplateResponse(request, "product_detail.html", {
            "title": product.name, "user": user, "product": product,
            "category_name": _category_label(product.category),
            "characteristic": product.characteristic or {},
            "price_history": price_history,
            "msg": request.query_params.get("msg"),
            "can_edit": user_has_permission(user, Permission.products_edit),
            "can_stock": user_has_permission(user, Permission.stock_change),
            "can_price": user_has_permission(user, Permission.price_change),
            **_page_context(user),
        })

    @app.get("/products/{product_id}/edit")
    def products_edit_page(product_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        can_edit = user_has_permission(user, Permission.products_edit)
        can_price = user_has_permission(user, Permission.price_change)
        can_stock = user_has_permission(user, Permission.stock_change)
        if not (can_edit or can_price or can_stock):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        product = get_product(db, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return templates.TemplateResponse(request, "product_form.html", {
            "title": f"Товар {product.name}", "user": user, "product": product, "is_new": False,
            "tree": get_category_tree(db),
            "characteristic_json": json.dumps(product.characteristic, ensure_ascii=False) if product.characteristic else "",
            "can_edit": can_edit, "can_price": can_price, "can_stock": can_stock,
            "min_stock_days_hint": get_int_setting(db, MIN_STOCK_DAYS_KEY, DEFAULT_MIN_STOCK_DAYS),
            **_page_context(user),
        })

    @app.post("/products/bulk-category")
    async def products_bulk_category_page(
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_edit)
        form = parse_qs((await request.body()).decode())
        product_ids = [v for v in (_to_int(x, None) for x in form.get("product_ids", [])) if v is not None]
        raw_category = (form.get("category_id") or [""])[0]
        if not product_ids:
            return RedirectResponse("/products?msg=Не выбрано ни одного товара", status_code=303)
        if not raw_category or not str(raw_category).isdigit():
            return RedirectResponse("/products?msg=Категория не выбрана", status_code=303)
        try:
            updated = change_bulk_category(db, product_ids, int(raw_category), user)
        except ValueError:
            return RedirectResponse("/products?msg=Категория не найдена", status_code=303)
        return RedirectResponse(f"/products?msg=Обновлено товаров: {updated}", status_code=303)

    @app.post("/products/{product_id}/delete")
    def products_delete_page(product_id: int, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_edit)
        try:
            delete_product(db, product_id, user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return RedirectResponse("/products?msg=Товар удалён", status_code=303)

    @app.post("/products/{product_id}/restore")
    def products_restore_page(product_id: int, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_edit)
        try:
            restore_product(db, product_id, user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return RedirectResponse("/products?include_deleted=1&msg=Товар восстановлен", status_code=303)

    @app.post("/products/{product_id}/stock")
    async def products_stock_page(
        product_id: int,
        request: Request,
        user: Annotated[User | None, Depends(_page_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.stock_change)
        form = parse_qs((await request.body()).decode())
        raw_rest = (form.get("rest") or [""])[0]
        rest = _to_int(raw_rest, 0)
        try:
            update_product(db, product_id, {"rest": rest}, user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return RedirectResponse(f"/products/{product_id}?msg=Остаток обновлён", status_code=303)

    @app.get("/categories")
    def categories_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_view)
        return templates.TemplateResponse(request, "categories.html", {
            "title": "Категории", "user": user, "tree": get_category_tree(db),
            "msg": request.query_params.get("msg"),
            "can_manage": user_has_permission(user, Permission.categories_manage),
            **_page_context(user),
        })

    @app.get("/categories/new")
    def categories_new_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.categories_manage)
        return templates.TemplateResponse(request, "category_form.html", {
            "title": "Новая категория", "user": user, "category": None, "is_new": True,
            "tree": get_category_tree(db),
            **_page_context(user),
        })

    @app.get("/categories/{category_id}/edit")
    def categories_edit_page(category_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.categories_manage)
        category = db.get(Category, category_id)
        if category is None:
            raise HTTPException(status_code=404, detail="Категория не найдена")
        return templates.TemplateResponse(request, "category_form.html", {
            "title": f"Категория {category.name}", "user": user, "category": category, "is_new": False,
            "tree": get_category_tree(db),
            **_page_context(user),
        })

    @app.get("/customers")
    def customers_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.customers_manage)
        search = request.query_params.get("search") or None
        include_deleted = request.query_params.get("include_deleted") == "1"
        raw_page = request.query_params.get("page") or "1"
        page = _page_param(raw_page)
        items, total = list_customers(db, search=search, include_deleted=include_deleted, page=page)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        customers = [{
            "id": c.id, "name": c.name, "inn": c.inn, "phone": c.phone,
            "email": c.email, "personal_discount": c.personal_discount,
            "is_deleted": c.is_deleted,
        } for c in items]
        qs = urlencode({k: v for k, v in {
            "search": search, "include_deleted": "1" if include_deleted else None,
        }.items() if v not in (None, "")})
        return templates.TemplateResponse(request, "customers.html", {
            "title": "Клиенты", "user": user, "customers": customers, "total": total,
            "page": page, "pages": pages, "base_qs": qs,
            "search": search or "", "include_deleted": include_deleted,
            "msg": request.query_params.get("msg"),
            "can_edit": user_has_permission(user, Permission.customers_manage),
            **_page_context(user),
        })

    @app.get("/customers/new")
    def customers_new_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.customers_manage)
        return templates.TemplateResponse(request, "customer_form.html", {
            "title": "Новый клиент", "user": user, "customer": None, "is_new": True,
            "can_edit": True, **_page_context(user),
        })

    @app.get("/customers/{customer_id}")
    def customers_detail_page(customer_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.customers_manage)
        customer = get_customer(db, customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        orders, _ = list_orders(db, customer_id=customer_id)
        stats = customer_order_stats(db, customer_id)
        return templates.TemplateResponse(request, "customer_detail.html", {
            "title": customer.name, "user": user, "customer": customer,
            "orders": orders, "stats": stats, "status_labels": STATUS_LABELS,
            "msg": request.query_params.get("msg"),
            "can_edit": user_has_permission(user, Permission.customers_manage),
            **_page_context(user),
        })

    @app.get("/customers/{customer_id}/edit")
    def customers_edit_page(customer_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.customers_manage)
        customer = get_customer(db, customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return templates.TemplateResponse(request, "customer_form.html", {
            "title": f"Клиент {customer.name}", "user": user, "customer": customer,
            "is_new": False, "can_edit": True, **_page_context(user),
        })

    @app.post("/customers/{customer_id}/delete")
    def customers_delete_page(customer_id: int, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.customers_manage)
        try:
            delete_customer(db, customer_id, user=user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return RedirectResponse("/customers?msg=Клиент удалён", status_code=303)

    @app.post("/customers/{customer_id}/restore")
    def customers_restore_page(customer_id: int, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.customers_manage)
        try:
            restore_customer(db, customer_id, user=user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return RedirectResponse("/customers?include_deleted=1&msg=Клиент восстановлен", status_code=303)

    @app.get("/orders")
    def orders_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_view)
        search = request.query_params.get("search") or None
        status = request.query_params.get("status") or None
        if status not in VALID_STATUSES:
            status = None
        raw_customer = request.query_params.get("customer_id")
        customer_id = _to_int(raw_customer, None)
        d_from = _parse_date(request.query_params.get("date_from"))
        d_to = _parse_date(request.query_params.get("date_to"))
        raw_amount = request.query_params.get("min_amount")
        try:
            min_amount = float(raw_amount) if raw_amount else None
        except ValueError:
            min_amount = None
        include_deleted = request.query_params.get("include_deleted") == "1"
        raw_page = request.query_params.get("page") or "1"
        page = _page_param(raw_page)
        items, total = list_orders(
            db, search=search, customer_id=customer_id, status=status,
            date_from=datetime.combine(d_from, time.min) if d_from else None,
            date_to=datetime.combine(d_to, time.max) if d_to else None,
            min_amount=min_amount, include_deleted=include_deleted, page=page,
        )
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        orders = [{
            "id": o.id, "number": o.number,
            "customer_name": (db.get(Customer, o.customer_id).name
                              if db.get(Customer, o.customer_id) else ""),
            "status": o.status, "total_amount": o.total_amount, "is_deleted": o.deleted_at is not None,
            "created_at": o.created_at.strftime("%Y-%m-%d") if o.created_at else "",
        } for o in items]
        base_qs = urlencode({k: v for k, v in {
            "search": search, "customer_id": customer_id, "status": status,
            "date_from": request.query_params.get("date_from"),
            "date_to": request.query_params.get("date_to"),
            "min_amount": request.query_params.get("min_amount"),
            "include_deleted": "1" if include_deleted else None,
        }.items() if v not in (None, "")})
        return templates.TemplateResponse(request, "orders.html", {
            "title": "Заказы", "user": user, "orders": orders, "total": total,
            "page": page, "pages": pages, "base_qs": base_qs,
            "search": search or "", "status": status, "customer_id": customer_id,
            "date_from": request.query_params.get("date_from", ""),
            "date_to": request.query_params.get("date_to", ""),
            "min_amount": min_amount or "", "include_deleted": include_deleted,
            "msg": request.query_params.get("msg"),
            "clients": _client_options(db, include_deleted=include_deleted),
            "statuses": list(VALID_STATUSES), "status_labels": STATUS_LABELS,
            "can_create": user_has_permission(user, Permission.orders_create),
            **_page_context(user),
        })

    @app.get("/orders/new")
    def orders_new_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_create)
        clients = _client_options(db)
        products = _product_options(db)
        return templates.TemplateResponse(request, "order_form.html", {
            "title": "Новый заказ", "user": user, "order": None, "is_new": True,
            "clients": clients, "products": products, "msg": request.query_params.get("msg"),
            **_page_context(user),
        })

    @app.post("/orders/new")
    async def orders_new_post(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_create)
        form = parse_qs((await request.body()).decode())
        raw_customer = (form.get("customer_id") or [""])[0]
        if not raw_customer.isdigit():
            return RedirectResponse("/orders/new?msg=Выберите клиента", status_code=303)
        raw_discount = (form.get("discount") or ["0"])[0]
        try:
            discount = float(raw_discount or 0)
        except ValueError:
            discount = 0.0
        items = _order_form_items(form)
        if not items:
            return RedirectResponse("/orders/new?msg=Добавьте хотя бы одну позицию", status_code=303)
        try:
            order = create_order(db, customer_id=int(raw_customer), discount=discount,
                                 items=items, user=user)
        except ValueError as e:
            return RedirectResponse("/orders/new?" + urlencode({"msg": str(e)}), status_code=303)
        return RedirectResponse(f"/orders/{order.id}?msg=Заказ создан", status_code=303)

    @app.get("/orders/{order_id}")
    def orders_detail_page(order_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_view)
        order = get_order(db, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        customer = db.get(Customer, order.customer_id)
        items = [{
            "product_name": it.product_name_snapshot,
            "price": it.product_price_snapshot, "quantity": it.quantity,
            "discount": it.discount_snapshot, "unit": it.unit_snapshot,
            "total": round(it.quantity * it.product_price_snapshot * (1 - it.discount_snapshot / 100.0), 2),
        } for it in order.items]
        history_rows = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "order", AuditLog.entity_id == order_id)
            .order_by(AuditLog.created_at.desc())
            .limit(20)
            .all()
        )
        usernames = {u.id: u.username for u in db.query(User).order_by(User.id).all()}
        history = [
            {
                "action": h.action,
                "username": usernames.get(h.user_id) or "—",
                "created_at": h.created_at.strftime("%Y-%m-%d %H:%M") if h.created_at else "",
                "old_value": h.old_value or "",
                "new_value": h.new_value or "",
            }
            for h in history_rows
        ]
        return templates.TemplateResponse(request, "order_detail.html", {
            "title": f"Заказ {order.number}", "user": user, "order": order,
            "customer_name": customer.name if customer else "",
            "is_deleted": order.deleted_at is not None,
            "items": items, "history": history,
            "status_label": STATUS_LABELS.get(order.status, order.status),
            "next_statuses": sorted(NEXT_POSSIBLE.get(order.status, set())),
            "status_labels": STATUS_LABELS,
            "msg": request.query_params.get("msg"),
            "can_status": user_has_permission(user, Permission.orders_status),
            "can_edit": user_has_permission(user, Permission.orders_edit),
            "can_delete": user_has_permission(user, Permission.orders_delete),
            "can_create": user_has_permission(user, Permission.orders_create),
            **_page_context(user),
        })

    @app.get("/orders/{order_id}/print")
    def orders_print_page(order_id: int,
                          user: Annotated[User | None, Depends(_page_user)] = None,
                          db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_view)
        order = get_order(db, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        from app.modules.printing import service as print_service
        ctx = print_service.build_order_context(order, db)
        ctx["order_id"] = order_id
        ctx["can_send_notifications"] = user_has_permission(user, Permission.notifications_send)
        html = print_service.JINJA.get_template("documents/order_print.html").render(**ctx)
        return Response(html, media_type="text/html")

    @app.post("/orders/{order_id}/send")
    async def orders_send_page(order_id: int, request: Request,
                               user: Annotated[User | None, Depends(_page_user)] = None,
                               db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.notifications_send)
        order = get_order(db, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        from app.modules.notifications.service import send_document
        form = parse_qs((await request.body()).decode())
        channel = (form.get("channel") or [""])[0]
        try:
            send_document(db, order, channel, user)
        except ValueError as e:
            return RedirectResponse(f"/orders/{order_id}/print?" + urlencode({"msg": str(e)}), status_code=303)
        return RedirectResponse(f"/orders/{order_id}/print?msg=Отправлено ({channel})", status_code=303)

    @app.get("/orders/{order_id}/pdf")
    def orders_pdf_download(order_id: int,
                            user: Annotated[User | None, Depends(_page_user)] = None,
                            db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_view)
        order = get_order(db, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        return Response(
            render_order_pdf(order, db),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="order-{order.number}.pdf"'},
        )

    @app.get("/orders/{order_id}/edit")
    def orders_edit_page(order_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_edit)
        order = get_order(db, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        if order.status == "done" or order.deleted_at is not None:
            return RedirectResponse(
                f"/orders/{order_id}?" + urlencode({"msg": "Нельзя редактировать выполненный заказ"}),
                status_code=303)
        clients = _client_options(db)
        products = _product_options(db)
        return templates.TemplateResponse(request, "order_form.html", {
            "title": f"Заказ {order.number}", "user": user, "order": order, "is_new": False,
            "clients": clients, "products": products, "msg": request.query_params.get("msg"),
            **_page_context(user),
        })

    @app.post("/orders/{order_id}/edit")
    async def orders_edit_post(order_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_edit)
        form = parse_qs((await request.body()).decode())
        raw_customer = (form.get("customer_id") or [""])[0]
        if not raw_customer.isdigit():
            return RedirectResponse(f"/orders/{order_id}?msg=Выберите клиента", status_code=303)
        raw_discount = (form.get("discount") or ["0"])[0]
        try:
            discount = float(raw_discount or 0)
        except ValueError:
            discount = 0.0
        items = _order_form_items(form)
        if not items:
            return RedirectResponse(f"/orders/{order_id}?msg=Добавьте хотя бы одну позицию", status_code=303)
        try:
            update_order(db, order_id, customer_id=int(raw_customer), discount=discount,
                         items=items, user=user)
        except ValueError as e:
            if str(e) in ("order not found", "customer not found"):
                raise HTTPException(status_code=404, detail=str(e))
            return RedirectResponse(f"/orders/{order_id}?" + urlencode({"msg": str(e)}), status_code=303)
        return RedirectResponse(f"/orders/{order_id}?msg=Заказ обновлён", status_code=303)

    @app.post("/orders/{order_id}/status")
    async def orders_status_page(order_id: int, request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_status)
        form = parse_qs((await request.body()).decode())
        new_status = (form.get("status") or [""])[0]
        try:
            change_status(db, order_id, new_status, user=user)
        except ValueError as e:
            if str(e) == "order not found":
                raise HTTPException(status_code=404, detail="Заказ не найден")
            return RedirectResponse(f"/orders/{order_id}?" + urlencode({"msg": str(e)}), status_code=303)
        return RedirectResponse(f"/orders/{order_id}?msg=Статус обновлён", status_code=303)

    @app.post("/orders/{order_id}/copy")
    def orders_copy_page(order_id: int, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_create)
        try:
            order = copy_order(db, order_id, user=user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        return RedirectResponse(f"/orders/{order.id}?msg=Заказ скопирован", status_code=303)

    @app.post("/orders/{order_id}/delete")
    def orders_delete_page(order_id: int, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_delete)
        try:
            delete_order(db, order_id, user=user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        return RedirectResponse("/orders?msg=Заказ удалён", status_code=303)

    @app.post("/orders/{order_id}/restore")
    def orders_restore_page(order_id: int, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.orders_delete)
        try:
            restore_order(db, order_id, user=user)
        except ValueError:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        return RedirectResponse(f"/orders/{order_id}?msg=Заказ восстановлен", status_code=303)

    return app


def _perms_groups(selected: list[str]) -> list[tuple[str, list[dict]]]:
    checked = set(selected)
    groups: dict[str, list[dict]] = {}
    for code, label in PERMISSION_LABELS.items():
        groups.setdefault(code.split(".")[0], []).append(
            {"code": code, "label": label, "checked": code in checked}
        )
    return list(groups.items())


app = create_app()