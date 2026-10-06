import re
import smtplib
from email.message import EmailMessage

from app.config import settings
from app.modules.settings.service import get_setting

SMTP_HOST_KEY = "smtp_host"
SMTP_PORT_KEY = "smtp_port"
SMTP_USER_KEY = "smtp_user"
SMTP_PASSWORD_KEY = "smtp_password"
SMTP_FROM_KEY = "smtp_from"
SMTP_PORT_MIN = 1
SMTP_PORT_MAX = 65535
"""Порты TCP, где 16 бит, — границы не выдуманы. На запись они проверяются как
диапазон, при чтении мусор из таблицы настроек отбрасывается, а не подгоняется."""


def smtp_port(db=None) -> int:
    """Порт из настроек, `.env`, а если оба не заданы — из `config.py`."""
    for candidate in (get_setting(db, SMTP_PORT_KEY) if db is not None else None,
                      settings.smtp_port):
        try:
            port = int(str(candidate).strip())
        except (TypeError, ValueError):
            continue
        if SMTP_PORT_MIN <= port <= SMTP_PORT_MAX:
            return port
    return settings.smtp_port


def smtp_config(db=None) -> dict:
    """Настройки отправки писем: сначала настройки приложения, потом `.env`.

    `.env` остаётся запасным путём, а не заменой настроек. На машине, где SMTP уже
    заполнен в файле, значения оттуда не пропадают — иначе задача молча сломала бы
    отправку там, где она работала. Поэтому пустое поле означает «не задано» и
    уводит к `.env`, а не «очистить»; очистить из интерфейса `.env` нельзя, и это
    сказано под полем на странице настроек.

    Ключи живут здесь, а не в `settings.service`, по прецеденту Telegram: там же
    лежат `TELEGRAM_TOKEN_KEY` и `TELEGRAM_CHAT_KEY`, и импорт `notifications.email`
    из `settings.service` был бы циклом.
    """
    def read(key: str, fallback: str) -> str:
        raw = str(get_setting(db, key) or "").strip() if db is not None else ""
        return raw or fallback

    return {
        "host": read(SMTP_HOST_KEY, settings.smtp_host),
        "port": smtp_port(db),
        "user": read(SMTP_USER_KEY, settings.smtp_user),
        "password": read(SMTP_PASSWORD_KEY, settings.smtp_password),
        "sender": read(SMTP_FROM_KEY, settings.smtp_from),
    }


def valid_port(raw: str) -> str | None:
    """Нормализованный порт, `""` для «не задано» и `None` для мусора.

    Отказ, а не усечение: молча подогнанный под диапазон порт выглядел бы на
    странице как сохранённый и вёл бы себя не так, как показано в поле. `int()`
    тут недостаточно — он согласится на «+587» и «٥٨٧», а `re` режет всё лишнее
    целиком.

    Пустое поле — не ошибка, а «не задано»: оно уводит к `SMTP_PORT` из `.env`,
    как и остальные четыре поля. Запрещать пустое здесь означало бы, что очистить
    порт из интерфейса нельзя, а четыре остальных можно.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.fullmatch(r"[0-9]{1,5}", raw):
        return None
    return raw if SMTP_PORT_MIN <= int(raw) <= SMTP_PORT_MAX else None


def send_email(to: str, subject: str, body: str, *, attachment: bytes | None = None,
               filename: str = "document.pdf", attachment_subtype: str = "pdf",
               db=None) -> None:
    config = smtp_config(db)
    if not config["host"]:
        return
    msg = EmailMessage()
    msg["From"] = config["sender"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application",
                           subtype=attachment_subtype, filename=filename)

    with smtplib.SMTP(config["host"], config["port"], timeout=10) as smtp:
        smtp.starttls()
        if config["user"]:
            smtp.login(config["user"], config["password"])
        smtp.send_message(msg)