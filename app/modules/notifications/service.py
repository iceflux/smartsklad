import re

from app.config import settings
from app.formatting import human_date
from app.modules.audit.service import log_action
from app.modules.notifications.models import NotificationLog
from app.modules.printing.service import render_order_pdf
from app.modules.settings.service import get_setting
from app.modules.notifications.telegram import (
    TELEGRAM_CHAT_KEY,
    TELEGRAM_TOKEN_KEY,
)


def message_text(order, db) -> str:
    from app.modules.customers.models import Customer
    customer = db.get(Customer, order.customer_id)
    name = customer.name if customer else ""
    date = human_date(order.created_at)
    return f"Заказ № {order.number} от {date}\nКлиент: {name}\nСумма: {order.total_amount}"


def delivery_state(db) -> list[dict]:
    """Состояние каналов отправки — ровно то, что проверяет сама отправка.

    Справка обязана показывать «настроено / не настроено» на момент запроса, а не
    текстом в шаблоне: SMTP заводят и заполняют на ходу, и написанное в разметке
    «почта не работает» станет враньём в ту же минуту, как только появятся
    сервер и отправитель.

    Поэтому читаются те же значения, на которых стоят проверки отправки:
    `settings.smtp_host` в `send_document` и токен с chat_id в `telegram.py`.
    Остальные поля почты выводятся отдельными фактами и в готовность не входят:
    свёртка их в один вывод сделала бы страницу врущей при неполной настройке —
    например, пустой отправитель при заполненном сервере означает, что письмо
    уйдёт с пустым заголовком, но проверку отправки уже пройдёт.

    Пароль и сам токен не выводятся: в разделе настроек токен и так показан
    замаскированным.
    """
    token = str(get_setting(db, TELEGRAM_TOKEN_KEY) or "").strip()
    chat_id = str(get_setting(db, TELEGRAM_CHAT_KEY) or "").strip()
    return [
        {
            "key": "email",
            "title": "Письма по электронной почте",
            "ready": bool(settings.smtp_host),
            "unready": "Не настроено",
            "ready_label": "Настроено",
            "how": "Заполните переменные SMTP в файле .env на сервере и перезапустите приложение.",
            "facts": [
                ("Сервер", settings.smtp_host or "не задан"),
                ("Порт", str(settings.smtp_port)),
                ("Логин", settings.smtp_user or "не задан"),
                ("Отправитель", settings.smtp_from or "не задан"),
            ],
        },
        {
            "key": "telegram",
            "title": "Сообщения в Telegram",
            "ready": bool(token and chat_id),
            "unready": "Не настроено",
            "ready_label": "Настроено",
            "how": "Впишите токен бота и чат получателя в разделе «Настройки».",
            "facts": [
                ("Токен бота", "задан" if token else "не задан"),
                ("Чат получателя", "задан" if chat_id else "не задан"),
            ],
        },
    ]


def _log(db, *, channel, recipient, status, order, user, subject="", body="", error=None):
    entry = NotificationLog(
        channel=channel, recipient=recipient, subject=subject or None,
        text_body=body, document_type="order", document_id=order.id,
        status=status, error=error, user_id=user.id if user else None,
    )
    db.add(entry)
    db.commit()


def send_document(db, order, channel: str, user=None) -> None:
    from app.modules.customers.models import Customer
    from app.modules.notifications.email import send_email
    from app.modules.notifications.telegram import send_telegram_document
    text = message_text(order, db)
    customer = db.get(Customer, order.customer_id)
    if channel not in ("email", "telegram"):
        raise ValueError("unknown channel")
    try:
        if channel == "email":
            recipient = (customer.email or "").strip()
            if not recipient:
                raise ValueError("у клиента не указан email")
            if not settings.smtp_host:
                raise ValueError("SMTP не настроен")
            pdf = render_order_pdf(order, db)
            send_email(recipient, f"Заказ № {order.number}", text,
                       attachment=pdf, filename=f"order-{order.number}.pdf")
        else:
            recipient = str(get_setting(db, "telegram_chat_id") or "").strip()
            pdf = render_order_pdf(order, db)
            send_telegram_document(text, pdf, db, filename=f"order-{order.number}.pdf")
        _log(db, channel=channel, recipient=recipient, status="sent", order=order, user=user,
             subject="Заказ" if channel == "email" else None, body=text)
    except ValueError as e:
        db.rollback()
        raise
    except Exception as e:  # сетевые/HTTP ошибки
        db.rollback()
        _log(db, channel=channel, recipient="", status="failed", order=order, user=user,
             body=text, error=re.sub(r"/bot[^/]+/", "/bot***/", str(e)))
    log_action(db, action="notification.send", entity_type="order", entity_id=order.id,
               new_value=channel, user_id=user.id if user else None)