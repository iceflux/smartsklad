from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.modules.auth.dependencies import SESSION_COOKIE
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import create_admin_if_empty, logout

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

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"title": "СмартСклад", "user": None})

    @app.get("/login")
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"title": "Вход"})

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

    return app


app = create_app()