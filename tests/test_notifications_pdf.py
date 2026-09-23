import smtplib
from email import message_from_bytes

import httpx
import pytest

from app.config import settings
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.notifications.email import send_email
from app.modules.notifications.models import NotificationLog
from app.modules.notifications.telegram import send_telegram_document
from app.modules.orders.service import create_order
from app.modules.settings.service import set_setting


class _CapturingSMTP:
    sent = []

    def __init__(self, host, port, *, timeout=None):
        pass
    def starttls(self): pass
    def login(self, user, pw): pass
    def send_message(self, msg): self.sent.append(msg)
    def __enter__(self): return self
    def __exit__(self, *e): pass


@pytest.fixture
def capture(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "localhost")
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)
    _CapturingSMTP.sent = []
    return _CapturingSMTP.sent


def test_send_email_with_attachment_adds_pdf(capture):
    pdf = b"%PDF-1.7 fake order pdf content"
    send_email("buyer@example.com", "Заказ № 1", "тело", attachment=pdf, filename="order-1.pdf")
    assert len(capture) == 1
    msg = message_from_bytes(capture[0].as_bytes())
    parts = [p for p in msg.walk() if p.get_content_disposition() == "attachment"]
    assert len(parts) == 1
    part = parts[0]
    assert part.get_filename() == "order-1.pdf"
    assert part.get_content_type() == "application/pdf"
    assert part.get_payload(decode=True).startswith(b"%PDF")


def test_send_email_without_attachment_has_no_attachment(capture):
    send_email("buyer@example.com", "s", "body")
    msg = message_from_bytes(capture[0].as_bytes())
    assert not [p for p in msg.walk() if p.get_content_disposition() == "attachment"]
    assert b"body" in capture[0].as_bytes()  # текст тела сохранён


@pytest.fixture
def order_env(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=100.0, rest=50)
    c = Customer(name="ООО Строй", email="buyer@example.com")
    db_session.add_all([p, c])
    db_session.commit()
    set_setting(db_session, "telegram_bot_token", "1234567890:ABC")
    set_setting(db_session, "telegram_chat_id", "777")
    return create_order(db_session, customer_id=c.id,
                        items=[{"product_id": p.id, "quantity": 2, "discount": 0}])


def test_send_telegram_document_posts_multipart(monkeypatch):
    captured = {}
    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["files"] = kwargs.get("files")
        return httpx.Response(200, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    pdf = b"%PDF-1.7 fake"
    send_telegram_document("текст", pdf, None, token="tok", chat_id="42", filename="order-5.pdf")
    assert captured["url"] == "https://api.telegram.org/bottok/sendDocument"
    assert captured["data"]["chat_id"] == "42"
    assert captured["data"]["caption"] == "текст"
    name, data, ctype = captured["files"]["document"]
    assert name == "order-5.pdf" and data == pdf and ctype == "application/pdf"


def test_send_telegram_document_requires_config():
    with pytest.raises(ValueError):
        send_telegram_document("текст", b"%PDF", None)


def test_send_document_email_attaches_pdf(db_session, order_env, monkeypatch):
    from app.modules.notifications import service as notif_service
    o = order_env
    admin = User(username="boss", password_hash=hash_password("x"), role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(admin); db_session.commit()
    captured = {}
    def fake_email(to, subject, body, **kw):
        captured["to"], captured["body"] = to, body
        captured["attachment"] = kw.get("attachment")
        captured["filename"] = kw.get("filename")
    monkeypatch.setattr("app.modules.notifications.email.send_email", fake_email)
    # SMTP не трогаем: подменён на уровень сервиса; service проверит settings.smtp_host
    monkeypatch.setattr(settings, "smtp_host", "localhost")
    notif_service.send_document(db_session, o, "email", admin)
    assert captured["to"] == "buyer@example.com"
    assert captured["attachment"].startswith(b"%PDF")
    assert captured["filename"] == f"order-{o.number}.pdf"
    row = db_session.query(NotificationLog).order_by(NotificationLog.id.desc()).first()
    assert row.status == "sent"


def test_send_document_telegram_sends_pdf(db_session, order_env, monkeypatch):
    from app.modules.notifications import service as notif_service
    o = order_env
    admin = User(username="boss2", password_hash=hash_password("x"), role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(admin); db_session.commit()
    captured = {}
    def fake_doc(text, pdf, db=None, *, token=None, chat_id=None, filename="document.pdf"):
        captured["text"], captured["pdf"], captured["filename"] = text, pdf, filename
    monkeypatch.setattr("app.modules.notifications.telegram.send_telegram_document", fake_doc)
    notif_service.send_document(db_session, o, "telegram", admin)
    assert captured["text"] and captured["pdf"].startswith(b"%PDF")
    assert captured["filename"] == f"order-{o.number}.pdf"
    row = db_session.query(NotificationLog).order_by(NotificationLog.id.desc()).first()
    assert row.status == "sent"


def test_send_document_email_failed_scrubs_token(db_session, order_env, monkeypatch):
    """через telegram-канал: сетей нет — ошибка HTTP ошибок с токеном в URL"""
    from app.modules.notifications import service as notif_service
    o = order_env
    admin = User(username="boss3", password_hash=hash_password("x"), role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(admin); db_session.commit()
    def boom(text, pdf, db=None, **kw):
        raise httpx.HTTPStatusError("400 ups", request=httpx.Request("POST", "https://api.telegram.org/bot1234567890:ABC/sendDocument"), response=httpx.Response(400))
    monkeypatch.setattr("app.modules.notifications.telegram.send_telegram_document", boom)
    notif_service.send_document(db_session, o, "telegram", admin)
    row = db_session.query(NotificationLog).order_by(NotificationLog.id.desc()).first()
    assert row.status == "failed"
    assert "1234567890:ABC" not in (row.error or "")