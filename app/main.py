from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import SessionLocal
from app.modules.auth.service import create_admin_if_empty

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

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"title": "СмартСклад"})

    return app


app = create_app()