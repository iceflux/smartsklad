import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.modules.audit.router import router as audit_router
from app.modules.auth.dependencies import (
    SESSION_COOKIE, ensure_permissions, get_page_user as _page_user,
    page_context as _page_context, user_has_permission,
)
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.auth.roles_router import router as roles_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import create_admin_if_empty, logout
from app.modules.auth.users_router import router as users_router
import app.modules.inventory.models  # noqa: F401
import app.modules.customers.models  # noqa: F401
from app.modules.inventory.models import Category, PriceHistory
from app.modules.inventory.router import router as inventory_router
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

APP_DIR = Path(__file__).parent
PAGE_SIZE = 25


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
        yield

    app = FastAPI(title="СмартСклад", lifespan=lifespan)
    app.state.skip_seed_admin = False
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    app.state.templates = templates

    app.include_router(auth_router)
    app.include_router(roles_router, prefix="/api")
    app.include_router(users_router, prefix="/api")
    app.include_router(inventory_router, prefix="/api")
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
            {"title": "Пользователи", "user": user, "users": users_view, "roles": roles, "current_user": user, **_page_context(user)},
        )

    @app.get("/products")
    def products_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None, db: Annotated[Session, Depends(get_db)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.products_view)
        search = request.query_params.get("search") or None
        raw_category = request.query_params.get("category_id")
        category_id = int(raw_category) if raw_category and raw_category.isdigit() else None
        include_deleted = request.query_params.get("include_deleted") == "1"
        raw_page = request.query_params.get("page") or "1"
        page = max(1, int(raw_page)) if raw_page.isdigit() else 1
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
        product_ids = [int(v) for v in form.get("product_ids", []) if v.isdigit()]
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
        rest = int(raw_rest) if raw_rest.lstrip("-").isdigit() else 0
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