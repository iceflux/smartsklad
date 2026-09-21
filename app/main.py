from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.modules.auth.dependencies import SESSION_COOKIE, ensure_permissions
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.auth.roles_router import router as roles_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import create_admin_if_empty, get_user_by_token, logout
from app.modules.auth.users_router import router as users_router
import app.modules.inventory.models  # noqa: F401
from app.modules.inventory.router import router as inventory_router

APP_DIR = Path(__file__).parent


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

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"title": "СмартСклад", "user": None})

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
        return templates.TemplateResponse(request, "roles.html", {"title": "Роли", "user": user, "roles": roles})

    @app.get("/roles/new")
    def roles_new_page(request: Request, user: Annotated[User | None, Depends(_page_user)] = None):
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ensure_permissions(user, Permission.users_manage)
        return templates.TemplateResponse(
            request, "roles_edit.html",
            {"title": "Создание роли", "user": user, "role": None, "is_new": True, "groups": _perms_groups([])},
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
            {"title": f"Роль {role.name}", "user": user, "role": role, "is_new": False, "groups": _perms_groups(role.permissions or [])},
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
            {"title": "Пользователи", "user": user, "users": users_view, "roles": roles, "current_user": user},
        )

    return app


def _page_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return get_user_by_token(db, token)


def _perms_groups(selected: list[str]) -> list[tuple[str, list[dict]]]:
    checked = set(selected)
    groups: dict[str, list[dict]] = {}
    for code, label in PERMISSION_LABELS.items():
        groups.setdefault(code.split(".")[0], []).append(
            {"code": code, "label": label, "checked": code in checked}
        )
    return list(groups.items())


app = create_app()