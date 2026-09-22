from app.modules.audit.service import log_action
from app.modules.notifications.models import NotificationLog
from app.modules.settings.service import get_setting


def message_text(order, db) -> str:
    from app.modules.customers.models import Customer
    customer = db.get(Customer, order.customer_id)
    name = customer.name if customer else ""
    date = order.created_at.strftime("%d.%m.%Y") if order.created_at else ""
    return f"Заказ № {order.number} от {date}\nКлиент: {name}\nСумма: {order.total_amount}"


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
    from app.modules.notifications.telegram import send_telegram
    text = message_text(order, db)
    customer = db.get(Customer, order.customer_id)
    if channel not in ("email", "telegram"):
        raise ValueError("unknown channel")
    try:
        if channel == "email":
            recipient = (customer.email or "").strip()
            if not recipient:
                raise ValueError("у клиента не указан email")
            send_email(recipient, f"Заказ № {order.number}", text)
        else:
            recipient = str(get_setting(db, "telegram_chat_id") or "").strip()
            send_telegram(text)
        _log(db, channel=channel, recipient=recipient, status="sent", order=order, user=user,
             subject="Заказ" if channel == "email" else None, body=text)
    except ValueError as e:
        db.rollback()
        raise
    except Exception as e:  # сетевые/HTTP ошибки
        db.rollback()
        _log(db, channel=channel, recipient="", status="failed", order=order, user=user,
             body=text, error=str(e))
    log_action(db, action="notification.send", entity_type="order", entity_id=order.id,
               new_value=channel, user_id=user.id if user else None)