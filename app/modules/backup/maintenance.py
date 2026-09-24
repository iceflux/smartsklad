import asyncio
import time as clock

from fastapi.responses import HTMLResponse, JSONResponse

STATE = {"mode": "idle", "starts_at": None}

ALLOWED_WHILE_RUNNING = ("/static", "/api/backup/status")


def maintenance_state() -> dict:
    if STATE["mode"] != "countdown":
        return {"mode": STATE["mode"], "starts_at": STATE["starts_at"], "seconds_left": 0}
    left = int(STATE["starts_at"] - clock.time())
    if left <= 0:
        return {"mode": "countdown", "starts_at": STATE["starts_at"], "seconds_left": 0}
    return {"mode": "countdown", "starts_at": STATE["starts_at"], "seconds_left": left}


def begin_countdown(seconds: int) -> dict:
    if seconds < 10 or seconds > 3600:
        raise ValueError("ожидание от 10 до 3600 секунд")
    STATE["mode"] = "countdown"
    STATE["starts_at"] = clock.time() + seconds
    return maintenance_state()


def cancel_countdown() -> None:
    set_idle()


def set_running() -> None:
    STATE["mode"] = "running"
    STATE["starts_at"] = None


def set_idle() -> None:
    STATE["mode"] = "idle"
    STATE["starts_at"] = None


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
    if STATE["mode"] == "running" and not request.url.path.startswith(ALLOWED_WHILE_RUNNING):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "идёт восстановление из резервной копии"}, status_code=503)
        return _page()
    return await call_next(request)


async def arm_restore(filename: str, delay: int, *, user_id: int | None = None) -> dict:
    from app.modules.backup.service import perform_restore

    begin_countdown(delay)
    await asyncio.sleep(delay)
    return await asyncio.to_thread(perform_restore, filename, user_id=user_id)
