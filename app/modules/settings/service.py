from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.settings.models import Setting

STOCK_TRACK_DEFAULT_KEY = "stock_track_default"


def _defaults() -> dict[str, str]:
    return {
        STOCK_TRACK_DEFAULT_KEY: "true",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    }


def ensure_default_settings(db: Session) -> None:
    for key, value in _defaults().items():
        if db.query(Setting).filter(Setting.key == key).first() is None:
            db.add(Setting(key=key, value=value))
    db.commit()


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
    log_action(db, action="settings.update", entity_type="settings", entity_id=row.id,
               old_value=old, new_value=value, user_id=user.id if user else None)