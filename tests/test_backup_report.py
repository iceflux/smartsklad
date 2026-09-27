from datetime import datetime

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.backup.scheduler import due_for_report
from app.modules.backup.report import (
    parse_recipients,
    read_report_settings,
    send_report,
    write_report_settings,
)
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.modules.notifications.models import NotificationLog
from app.modules.settings.service import (
    REPORT_ENABLED_KEY,
    REPORT_PERIOD_KEY,
    REPORT_RECIPIENTS_KEY,
    REPORT_TIME_KEY,
    set_setting,
)
from app.modules.inventory.service import get_unit_by_name

PASSWORD = "password123"


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


@pytest.fixture
def smtp_ready(monkeypatch):
    monkeypatch.setattr("app.modules.backup.report.app_settings.smtp_host", "localhost")


def _user(db_session, username, email):
    u = User(username=username, password_hash=hash_password(PASSWORD), role_id=1, email=email)
    db_session.add(u)
    db_session.commit()
    return u


def _sale(db_session, number):
    c = Customer(name="ООО Строй", personal_discount=0.0)
    p = Product(name="Гвозди", sku=f"SKU-R-{number}", unit_id=_unit_id(db_session), price=10.0, rest=10)
    db_session.add(c)
    db_session.add(p)
    db_session.commit()
    o = Order(number=number, customer_id=c.id, status="done", discount=0.0,
              total_amount=100.0, created_at=datetime.now())
    db_session.add(o)
    db_session.commit()
    o.items.append(OrderItem(product_id=p.id, product_name_snapshot="Гвозди",
                             product_price_snapshot=10.0, discount_snapshot=0.0,
                             quantity=2, unit_snapshot="шт"))
    db_session.commit()
    return o


def test_parse_recipients_handles_garbage():
    assert parse_recipients(None) == []
    assert parse_recipients("не json") == []
    assert parse_recipients('["1", "2"]') == [1, 2]
    assert parse_recipients('[1, "x", 3]') == [1, 3]


def test_read_report_settings_defaults(db_session):
    settings = read_report_settings(db_session)
    assert settings["enabled"] is False
    assert settings["period"] == "week"
    assert settings["send_time"] == "09:00"
    assert settings["recipient_ids"] == []


def test_write_report_settings_rejects_bad_period(db_session):
    with pytest.raises(ValueError):
        write_report_settings(db_session, enabled=True, period="year",
                              send_time="09:00", recipient_ids=[])


def test_write_report_settings_rejects_bad_time(db_session):
    with pytest.raises(ValueError):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time="25:99", recipient_ids=[])


def test_write_report_settings_rejects_bad_recipient(db_session):
    with pytest.raises(ValueError):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time="09:00", recipient_ids=["не число"])


def test_write_report_settings_rejects_unknown_recipient(db_session):
    with pytest.raises(ValueError, match="получатель не найден"):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time="09:00", recipient_ids=[999])


def test_write_report_settings_rejects_inactive_recipient(db_session):
    user = _user(db_session, "inactive", "inactive@example.com")
    user.is_active = False
    db_session.commit()
    with pytest.raises(ValueError, match="получатель неактивен"):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time="09:00", recipient_ids=[user.id])


def test_write_report_settings_rejects_malformed_address(db_session):
    user = _user(db_session, "bad", "not-an-email")
    with pytest.raises(ValueError, match="некорректный email"):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time="09:00", recipient_ids=[user.id])


def test_write_report_settings_rejects_non_string_time(db_session):
    with pytest.raises(ValueError):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time=900, recipient_ids=[])


def test_write_report_settings_rejects_non_iterable_recipients(db_session):
    with pytest.raises(ValueError):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time="09:00", recipient_ids=42)


def test_write_report_settings_rejects_string_recipients(db_session):
    with pytest.raises(ValueError):
        write_report_settings(db_session, enabled=True, period="week",
                              send_time="09:00", recipient_ids="7")


def test_write_report_settings_persists_and_attributes_audit(db_session):
    from app.modules.audit.models import AuditLog
    actor = _user(db_session, "boss", "boss@example.com")
    result = write_report_settings(db_session, enabled=True, period="month",
                                   send_time="08:15",
                                   recipient_ids=[str(actor.id)], user_id=actor.id)
    assert result["enabled"] is True
    assert result["period"] == "month"
    assert result["send_time"] == "08:15"
    assert result["recipient_ids"] == [actor.id]
    entry = db_session.query(AuditLog).filter(AuditLog.action == "report.settings").one()
    assert entry.user_id == actor.id


def test_report_skips_user_without_email(db_session, monkeypatch, smtp_ready):
    _user(db_session, "boss", "boss@example.com")
    without = _user(db_session, "nolog", None)
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d]' % without.id)
    sent = []
    monkeypatch.setattr("app.modules.backup.report.send_email",
                        lambda *a, **k: sent.append(a[0]))
    result = send_report(db_session, now=datetime(2026, 9, 25, 9, 0))
    assert result["sent"] == 0
    assert result["skipped"] == 1
    assert sent == []
    entry = db_session.query(NotificationLog).one()
    assert entry.status == "failed"
    assert entry.error
    assert entry.recipient == without.username
    assert entry.document_type == "report"
    assert entry.document_id is None


def test_report_sends_one_message_per_recipient(db_session, monkeypatch, smtp_ready):
    first = _user(db_session, "a", "a@example.com")
    second = _user(db_session, "b", "b@example.com")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d, %d]' % (first.id, second.id))
    sent = []
    calls = []

    def capture(to, *args, **kwargs):
        sent.append(to)
        calls.append(kwargs)

    monkeypatch.setattr("app.modules.backup.report.send_email", capture)
    result = send_report(db_session, now=datetime(2026, 9, 25, 9, 0))
    assert result["sent"] == 2
    assert sorted(sent) == ["a@example.com", "b@example.com"]
    assert all(call["attachment_subtype"] == "csv" for call in calls)
    assert all(call["filename"].startswith("sales-") and call["filename"].endswith(".csv")
               for call in calls)


def test_report_uses_configured_period_for_body_and_csv(db_session, monkeypatch, smtp_ready):
    user = _user(db_session, "boss", "boss@example.com")
    _sale(db_session, "00001")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_PERIOD_KEY, "day")
    set_setting(db_session, REPORT_TIME_KEY, "09:00")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d]' % user.id)
    captured = {}

    def capture(to, subject, body, **kwargs):
        captured["to"] = to
        captured["subject"] = subject
        captured["body"] = body
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.modules.backup.report.send_email", capture)
    now = datetime.now()
    result = send_report(db_session, now=now)
    assert result["sent"] == 1
    day = now.strftime("%d.%m.%Y")
    assert f"Период: {day} — {day}" in captured["body"]
    assert captured["subject"] == "Отчёт по продажам за день"
    assert "Заказов: 1" in captured["body"]
    assert captured["kwargs"]["attachment_subtype"] == "csv"
    assert captured["kwargs"]["filename"].endswith(".csv")
    csv_lines = captured["kwargs"]["attachment"].decode("utf-8-sig").splitlines()
    assert csv_lines == ["date;orders;amount", f"{now.date().isoformat()};1;100.0"]


def test_one_bad_address_does_not_break_others(db_session, monkeypatch, smtp_ready):
    good = _user(db_session, "good", "good@example.com")
    bad = _user(db_session, "bad", "не-почта")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d, %d]' % (bad.id, good.id))
    sent = []
    monkeypatch.setattr("app.modules.backup.report.send_email",
                        lambda *a, **k: sent.append(a[0]))
    result = send_report(db_session, now=datetime(2026, 9, 25, 9, 0))
    assert result["sent"] == 1
    assert result["skipped"] == 1
    assert sent == ["good@example.com"]
    entry = db_session.query(NotificationLog).filter(
        NotificationLog.status == "failed").one()
    assert entry.status == "failed"
    assert entry.error
    assert entry.document_type == "report"
    assert entry.document_id is None


def test_smtp_failure_does_not_stop_other_recipients(db_session, monkeypatch, smtp_ready):
    first = _user(db_session, "a", "a@example.com")
    second = _user(db_session, "b", "b@example.com")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d, %d]' % (first.id, second.id))
    sent = []

    def flaky(to, *args, **kwargs):
        if to == "a@example.com":
            raise OSError("connection refused")
        sent.append(to)

    monkeypatch.setattr("app.modules.backup.report.send_email", flaky)
    result = send_report(db_session, now=datetime(2026, 9, 25, 9, 0))
    assert result["sent"] == 1
    assert result["failed"] == 1
    assert sent == ["b@example.com"]
    entry = db_session.query(NotificationLog).filter(
        NotificationLog.status == "failed").one()
    assert entry.status == "failed"
    assert entry.error
    assert entry.document_type == "report"
    assert entry.document_id is None


def test_report_without_smtp_is_recorded_as_failed(db_session, monkeypatch):
    user = _user(db_session, "boss", "boss@example.com")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d]' % user.id)
    monkeypatch.setattr("app.modules.backup.report.app_settings.smtp_host", "")
    monkeypatch.setattr("app.modules.backup.report.send_email",
                        lambda *a, **k: pytest.fail("SMTP не настроен, отправка не должна выполняться"))
    result = send_report(db_session, now=datetime(2026, 9, 25, 9, 0))
    assert result["sent"] == 0
    assert result["failed"] == 1
    entry = db_session.query(NotificationLog).one()
    assert entry.status == "failed"
    assert "SMTP" in entry.error
    from app.modules.audit.models import AuditLog
    audit = db_session.query(AuditLog).filter(AuditLog.action == "report.send").one()
    assert audit.new_value == "sent=0 skipped=0"


def test_report_marks_when_sent(db_session, monkeypatch, smtp_ready):
    user = _user(db_session, "boss", "boss@example.com")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d]' % user.id)
    monkeypatch.setattr("app.modules.backup.report.send_email", lambda *a, **k: None)
    send_report(db_session, now=datetime(2026, 9, 25, 9, 0))
    from app.modules.backup.service import get_backup_settings
    row = get_backup_settings(db_session)
    assert row.last_report_sent == 1
    assert row.last_report_at is not None


def test_manual_send_does_not_advance_scheduled_report_cadence(db_session, monkeypatch, smtp_ready):
    from app.modules.backup.service import get_backup_settings
    from datetime import time

    user = _user(db_session, "boss", "boss@example.com")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_PERIOD_KEY, "week")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d]' % user.id)
    monkeypatch.setattr("app.modules.backup.report.send_email", lambda *a, **k: None)
    moment = datetime(2026, 9, 25, 9, 0)

    send_report(db_session, now=moment)
    row = get_backup_settings(db_session)

    assert row.last_report_at == moment
    assert row.last_report_scheduled_at is None
    assert due_for_report(True, time(9, 0), "week",
                          datetime(2026, 10, 1, 9, 0), row.last_report_scheduled_at) is True


def test_report_audit_uses_actor(db_session, monkeypatch, smtp_ready):
    from app.modules.audit.models import AuditLog
    recipient = _user(db_session, "recipient", "recipient@example.com")
    actor = _user(db_session, "actor", "actor@example.com")
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, '[%d]' % recipient.id)
    monkeypatch.setattr("app.modules.backup.report.send_email", lambda *a, **k: None)
    send_report(db_session, now=datetime(2026, 9, 25, 9, 0), user_id=actor.id)
    entry = db_session.query(AuditLog).filter(AuditLog.action == "report.send").one()
    assert entry.user_id == actor.id
