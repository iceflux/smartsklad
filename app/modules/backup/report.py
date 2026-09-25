import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.modules.audit.service import log_action
from app.modules.auth.models import User
from app.modules.backup.service import EMAIL_RE, get_backup_settings
from app.modules.dashboard.service import dashboard_metrics, dashboard_sales_csv, resolve_period
from app.modules.notifications.email import send_email
from app.modules.notifications.models import NotificationLog
from app.modules.settings.service import (
    REPORT_ENABLED_KEY,
    REPORT_PERIOD_KEY,
    REPORT_RECIPIENTS_KEY,
    REPORT_TIME_KEY,
    get_setting,
    set_setting,
)

PERIOD_LABELS = {"day": "день", "week": "неделю", "month": "месяц"}


def parse_recipients(raw: str | None) -> list[int]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def read_report_settings(db: Session) -> dict:
    period = get_setting(db, REPORT_PERIOD_KEY) or "week"
    return {
        "enabled": get_setting(db, REPORT_ENABLED_KEY) == "true",
        "period": period if period in PERIOD_LABELS else "week",
        "send_time": get_setting(db, REPORT_TIME_KEY) or "09:00",
        "recipient_ids": parse_recipients(get_setting(db, REPORT_RECIPIENTS_KEY)),
    }


def write_report_settings(db: Session, *, enabled, period, send_time,
                          recipient_ids, user_id: int | None = None) -> dict:
    if period not in PERIOD_LABELS:
        raise ValueError("период отчёта: day, week или month")
    if not isinstance(send_time, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", send_time):
        raise ValueError("время отправки в формате ЧЧ:ММ")
    if isinstance(recipient_ids, (str, bytes)) or not hasattr(recipient_ids, "__iter__"):
        raise ValueError("получатели: нужен список id")
    ids = []
    for value in recipient_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            raise ValueError("некорректный получатель")
    actor = db.get(User, user_id) if user_id else None
    set_setting(db, REPORT_ENABLED_KEY, "true" if enabled else "false", user=actor)
    set_setting(db, REPORT_PERIOD_KEY, period, user=actor)
    set_setting(db, REPORT_TIME_KEY, send_time, user=actor)
    set_setting(db, REPORT_RECIPIENTS_KEY, json.dumps(ids), user=actor)
    log_action(db, action="report.settings", entity_type="settings", entity_id=None,
               new_value=f"{period} {send_time} {ids}", user_id=user_id)
    return read_report_settings(db)


def _log(db: Session, *, recipient, status, subject, body, error=None) -> None:
    db.add(NotificationLog(channel="email", recipient=recipient, subject=subject,
                           text_body=body, document_type="report", document_id=None,
                           status=status, error=error))
    db.commit()


def send_report(db: Session, *, now: datetime | None = None,
                user_id: int | None = None) -> dict:
    now = now or datetime.now()
    settings_row = read_report_settings(db)
    start, end = resolve_period(settings_row["period"])
    metrics = dashboard_metrics(db, start, end)
    csv_bytes = dashboard_sales_csv(metrics["daily"])
    label = PERIOD_LABELS[settings_row["period"]]
    subject = f"Отчёт по продажам за {label}"
    body = (
        f"Период: {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}\n"
        f"Заказов: {metrics['orders_count']}\n"
        f"Сумма продаж: {metrics['total_sum']:.2f}\n"
        f"Средний чек: {metrics['avg_check']:.2f}\n"
    )
    result = {"sent": 0, "skipped": 0, "failed": 0, "period": settings_row["period"]}
    if not app_settings.smtp_host:
        _log(db, recipient="", status="failed", subject=subject, body=body,
             error="SMTP не настроен")
        result["failed"] = len(settings_row["recipient_ids"])
        _stamp(db, now, result)
        return result
    for recipient_id in settings_row["recipient_ids"]:
        recipient = db.get(User, recipient_id)
        address = ((recipient.email if recipient else "") or "").strip()
        if not address:
            result["skipped"] += 1
            _log(db, recipient=(recipient.username if recipient else ""),
                 status="failed", subject=subject, body=body,
                 error="у пользователя не указан email")
            continue
        if not EMAIL_RE.match(address):
            result["skipped"] += 1
            _log(db, recipient=address[:256], status="failed", subject=subject,
                 body=body, error="некорректный адрес email")
            continue
        try:
            send_email(address, subject, body, attachment=csv_bytes,
                       filename=f"sales-{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}.csv",
                       attachment_subtype="csv")
            result["sent"] += 1
            _log(db, recipient=address[:256], status="sent", subject=subject, body=body)
        except Exception as error:
            result["failed"] += 1
            _log(db, recipient=address[:256], status="failed", subject=subject,
                 body=body, error=str(error)[:512])
    _stamp(db, now, result)
    log_action(db, action="report.send", entity_type="settings", entity_id=None,
               new_value=f"sent={result['sent']} skipped={result['skipped']}",
               user_id=user_id)
    return result


def _stamp(db: Session, now: datetime, result: dict) -> None:
    row = get_backup_settings(db)
    row.last_report_at = now
    row.last_report_sent = result["sent"]
    row.last_report_skipped = result["skipped"]
    db.commit()
