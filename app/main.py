from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).parent


def create_app() -> FastAPI:
    app = FastAPI(title="СмартСклад")
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    app.state.templates = templates

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"title": "СмартСклад"})

    return app


app = create_app()