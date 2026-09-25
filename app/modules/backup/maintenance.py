import asyncio
import time as clock
from datetime import datetime

from fastapi.responses import HTMLResponse, JSONResponse

STATE = {"mode": "idle", "starts_at": None, "restore_task": None}

MIN_COUNTDOWN_SECONDS = 10
MAX_COUNTDOWN_SECONDS = 3600
ALLOWED_WHILE_RUNNING = ("/static", "/api/backup/status", "/backups/restore/cancel")


def maintenance_state() -> dict:
    starts_at_text = (datetime.fromtimestamp(STATE["starts_at"]).strftime("%H:%M:%S")
                      if STATE["mode"] == "countdown" and STATE["starts_at"] is not None else None)
    if STATE["mode"] != "countdown":
        return {"mode": STATE["mode"], "starts_at": STATE["starts_at"],
                "starts_at_text": starts_at_text, "seconds_left": 0}
    left = int(STATE["starts_at"] - clock.time())
    if left <= 0:
        return {"mode": "countdown", "starts_at": STATE["starts_at"],
                "starts_at_text": starts_at_text, "seconds_left": 0}
    return {"mode": "countdown", "starts_at": STATE["starts_at"],
            "starts_at_text": starts_at_text, "seconds_left": left}


def begin_countdown(seconds: int) -> dict:
    if not MIN_COUNTDOWN_SECONDS <= seconds <= MAX_COUNTDOWN_SECONDS:
        raise ValueError("ожидание от 10 до 3600 секунд")
    if STATE["mode"] in ("countdown", "running"):
        raise ValueError("восстановление уже запланировано")
    STATE["mode"] = "countdown"
    STATE["starts_at"] = clock.time() + seconds
    return maintenance_state()


def cancel_countdown() -> None:
    if STATE["mode"] == "countdown":
        task = STATE["restore_task"]
        STATE["restore_task"] = None
        if task is not None:
            task.cancel()
        set_idle()


def set_running() -> None:
    STATE["mode"] = "running"
    STATE["starts_at"] = None


def set_idle() -> None:
    STATE["mode"] = "idle"
    STATE["starts_at"] = None


def _allowed(path: str) -> bool:
    return any(path == entry or path.startswith(entry + "/") for entry in ALLOWED_WHILE_RUNNING)


def _page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<title>Обслуживание</title></head><body>"
        "<h1>Идёт восстановление из резервной копии</h1>"
        "<p>Приложение вернётся в работу автоматически. Не закрывайте эту страницу.</p>"
        "</body></html>",
        status_code=503,
    )


async def maintenance_middleware(request, call_next):
    if STATE["mode"] == "running" and not _allowed(request.url.path):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "идёт восстановление из резервной копии"}, status_code=503)
        return _page()
    return await call_next(request)


def arm_restore(filename: str, delay: int, *, user_id: int | None = None,
                countdown_started: bool = False) -> asyncio.Task:
    if not countdown_started:
        begin_countdown(delay)
    task = asyncio.create_task(_arm_restore(filename, delay, user_id=user_id))
    STATE["restore_task"] = task
    return task


async def _arm_restore(filename: str, delay: int, *, user_id: int | None) -> dict:
    try:
        from app.modules.backup.service import perform_restore

        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            set_idle()
            raise
        return await asyncio.to_thread(perform_restore, filename, user_id=user_id)
    finally:
        if STATE["restore_task"] is asyncio.current_task():
            STATE["restore_task"] = None
