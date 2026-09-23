import httpx

from app.modules.settings.service import get_setting

TELEGRAM_TOKEN_KEY = "telegram_bot_token"
TELEGRAM_CHAT_KEY = "telegram_chat_id"


def send_telegram(text: str, db=None, *, token=None, chat_id=None) -> None:
    token = token or (get_setting(db, TELEGRAM_TOKEN_KEY) if db else None)
    chat_id = chat_id or (get_setting(db, TELEGRAM_CHAT_KEY) if db else None)
    if not token or not chat_id:
        raise ValueError("telegram not configured")
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
    resp.raise_for_status()


def send_telegram_document(text: str, pdf: bytes, db=None, *, token=None, chat_id=None,
                           filename="document.pdf") -> None:
    token = token or (get_setting(db, TELEGRAM_TOKEN_KEY) if db else None)
    chat_id = chat_id or (get_setting(db, TELEGRAM_CHAT_KEY) if db else None)
    if not token or not chat_id:
        raise ValueError("telegram not configured")
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data={"chat_id": chat_id, "caption": text},
        files={"document": (filename, pdf, "application/pdf")},
        timeout=10,
    )
    resp.raise_for_status()