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
REPORT_ENABLED_KEY = "report_email_enabled"
REPORT_PERIOD_KEY = "report_email_period"
REPORT_TIME_KEY = "report_email_time"
REPORT_RECIPIENTS_KEY = "report_email_recipients"
REPORT_PERIODS = ("day", "week", "month")
SEARCH_SLOW_MS_KEY = "search_slow_ms"
DEFAULT_SEARCH_SLOW_MS = 1000
SEARCH_SLOW_MS_MIN = 200
SEARCH_SLOW_MS_MAX = 5000
SIDEBAR_COLLAPSED_KEY = "ui.sidebar_collapsed"
THEME_KEY = "ui.theme"
THEME_VALUES = ("light", "dark")
DEFAULT_THEME = "light"
USER_KEYS = (SIDEBAR_COLLAPSED_KEY, THEME_KEY)
COMPANY_FIELDS = (
    ("company_name", "Наименование"),
    ("company_inn", "ИНН"),
    ("company_kpp", "КПП"),
    ("company_address", "Адрес"),
    ("company_phone", "Телефон"),
    ("company_email", "Email"),
)


def company_data(db: Session) -> dict[str, str]:
    return {key: (get_setting(db, key) or "").strip() for key, _label in COMPANY_FIELDS}


def user_setting_key(key: str, user_id: int) -> str:
    return f"{key}:{user_id}"


def is_user_setting(key: str) -> bool:
    """Ключ на пользователя — это известное имя плюс «:» и хвост.

    Одного префикса `ui.` мало: глобальная настройка с таким же префиксом
    (она правдоподобна — интерфейсных глобалок будет много) молча ушла бы
    из общего списка вместе с меню. Поэтому сверяется и имя из USER_KEYS, и
    двоеточие. Проверки «хвост из цифр» здесь нет намеренно: отличить ею
    `ui.sidebar_collapsed:7` от глобалки, названной `ui.sidebar_collapsed:что-то`,
    нечем — такого глобалки нет и заводить её незачем, а непроверяемая ветка
    в предикате — тот же класс дефекта, что тест, который не может упасть.
    """
    base, sep, _user_id = key.rpartition(":")
    return bool(sep) and base in USER_KEYS


def _defaults() -> dict[str, str]:
    return {
        STOCK_TRACK_DEFAULT_KEY: "true",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        USER_PASSWORD_CHARSET_KEY: DEFAULT_PASSWORD_CHARSET,
        USER_PASSWORD_LENGTH_KEY: str(DEFAULT_PASSWORD_LENGTH),
        MIN_STOCK_DAYS_KEY: str(DEFAULT_MIN_STOCK_DAYS),
        REPORT_ENABLED_KEY: "false",
        REPORT_PERIOD_KEY: "week",
        REPORT_TIME_KEY: "09:00",
        REPORT_RECIPIENTS_KEY: "[]",
        SEARCH_SLOW_MS_KEY: str(DEFAULT_SEARCH_SLOW_MS),
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


def search_slow_ms(db: Session) -> int:
    """Порог, после которого окно поиска перестаёт искать само.

    1000 мс — не круглое число, взятое на глаз. Это граница из модели
    производительности RAIL (Google, «How users perceive performance delays»,
    со ссылкой на исследования Nielsen Norman Group): до 1000 мс задержка
    читается как «естественное и непрерывное продолжение задачи», а начиная
    с 1000 мс «пользователь теряет фокус на задаче, которую выполняет». Тот
    же источник отмечает, что пользователи мобильных устройств обычно
    терпеливее, поэтому для телефона 1000 мс — консервативная граница.

    Цель RAIL «respond» в 100 мс здесь НЕ применима: она про реакцию на
    нажатие, а не про запрос, возвращающий новые результаты, — то есть про
    смену задачи.

    Границы: снизу 200 мс, потому что ниже порог перестаёт быть страховкой и
    становится помехой — один холодный кэш или соседний `pg_dump`, и живой
    поиск выключен навсегда. Сверху 5000 мс, потому что 5 с — это уже заведомо
    не «ощущение задержки», а сломанная настройка: при пороге в минуту человек
    будет смотреть на пустой список, так и не получив кнопку «Найти».
    """
    return get_int_setting(db, SEARCH_SLOW_MS_KEY, DEFAULT_SEARCH_SLOW_MS)


def sidebar_collapsed(db: Session, user_id: int) -> bool:
    return get_setting(db, user_setting_key(SIDEBAR_COLLAPSED_KEY, user_id)) == "true"


def set_sidebar_collapsed(db: Session, user_id: int, collapsed: bool, *, user=None) -> None:
    set_setting(db, user_setting_key(SIDEBAR_COLLAPSED_KEY, user_id),
                "true" if collapsed else "false", user=user)


def user_theme(db: Session, user_id: int) -> str:
    value = get_setting(db, user_setting_key(THEME_KEY, user_id))
    return value if value in THEME_VALUES else DEFAULT_THEME


def set_user_theme(db: Session, user_id: int, value: str, *, user=None) -> None:
    if value not in THEME_VALUES:
        raise ValueError("неизвестная тема")
    set_setting(db, user_setting_key(THEME_KEY, user_id), value, user=user)
