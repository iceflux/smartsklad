from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.settings.models import Setting

STOCK_TRACK_DEFAULT_KEY = "stock_track_default"
USER_PASSWORD_CHARSET_KEY = "user_password_charset"
USER_PASSWORD_LENGTH_KEY = "user_password_length"
DEFAULT_PASSWORD_CHARSET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_!@#$%^&*"
DEFAULT_PASSWORD_LENGTH = 12
MIN_STOCK_DAYS_KEY = "min_stock_days"
DEFAULT_MIN_STOCK_DAYS = 14


def _defaults() -> dict[str, str]:
    return {
        STOCK_TRACK_DEFAULT_KEY: "true",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        USER_PASSWORD_CHARSET_KEY: DEFAULT_PASSWORD_CHARSET,
        USER_PASSWORD_LENGTH_KEY: str(DEFAULT_PASSWORD_LENGTH),
        MIN_STOCK_DAYS_KEY: str(DEFAULT_MIN_STOCK_DAYS),
    }


def ensure_default_settings(db: Session) -> None:
    for key, value in _defaults().items():
        if db.query(Setting).filter(Setting.key == key).first() is None:
            db.add(Setting(key=key, value=value))
    db.commit()


def mask_telegram_token(value: str | None) -> str | None:
    if not value:
        return value
    return (value[:3] + "****" + value[-3:]) if len(value) > 6 else "****"


def get_setting(db: Session, key: str) -> str | None:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else None


def set_setting(db: Session, key: str, value: str, *, user=None) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    old = row.value if row else None
    if row is None:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()
    if key == "telegram_bot_token":
        old = mask_telegram_token(old)
        value = mask_telegram_token(value)
    log_action(db, action="settings.update", entity_type="settings", entity_id=row.id,
               old_value=old, new_value=value, user_id=user.id if user else None)


def get_int_setting(db: Session, key: str, default: int) -> int:
    raw = get_setting(db, key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
