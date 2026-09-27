import httpx
import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.notifications.models import NotificationLog
from app.modules.notifications.service import message_text, send_document
from app.modules.orders.service import create_order
from app.modules.settings.service import set_setting
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


PASSWORD = "password123"
TELEGRAM_TOKEN_KEY = "telegram_bot_token"
TELEGRAM_CHAT_KEY = "telegram_chat_id"


@pytest.fixture
def env(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=50)
    c = Customer(name="ООО Строй", email="a@b.ru")
    db_session.add_all([p, c])
    db_session.commit()
    o = create_order(db_session, customer_id=c.id, items=[{"product_id": p.id, "quantity": 2, "discount": 0}])
    return c, o


def _user(db_session, username="заказчик"):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    return u


def test_message_text_mentions_order_and_customer(db_session, env):
    c, o = env
    text = message_text(o, db_session)
    assert o.number in text
    assert "ООО Строй" in text


def test_send_document_email_writes_log(db_session, env, monkeypatch):
    c, o = env
    u = _user(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    calls = []

    def fake_send(to, subject, body, **kw):
        calls.append((to, subject, body))

    monkeypatch.setattr("app.modules.notifications.email.send_email", fake_send)
    send_document(db_session, o, "email", u)
    row = db_session.query(NotificationLog).one()
    assert row.channel == "email"
    assert row.recipient == "a@b.ru"
    assert row.status == "sent"
    assert row.document_id == o.id
    assert row.user_id == u.id
    assert len(calls) == 1 and calls[0][0] == "a@b.ru"


def test_send_document_email_smtp_not_configured(db_session, env):
    c, o = env
    u = _user(db_session)
    with pytest.raises(ValueError, match="SMTP"):
        send_document(db_session, o, "email", u)
    assert db_session.query(NotificationLog).count() == 0


def test_send_document_email_no_customer_email(db_session, env):
    c, o = env
    c.email = None
    db_session.commit()
    u = _user(db_session)
    with pytest.raises(ValueError):
        send_document(db_session, o, "email", u)


def test_send_document_telegram_writes_log(db_session, env, monkeypatch):
    c, o = env
    set_setting(db_session, TELEGRAM_TOKEN_KEY, "tok123", user=None)
    set_setting(db_session, TELEGRAM_CHAT_KEY, "777", user=None)
    u = _user(db_session)
    sent = []

    def fake_telegram(text, pdf, db=None, **kw):
        sent.append(text)

    monkeypatch.setattr("app.modules.notifications.telegram.send_telegram_document", fake_telegram)
    send_document(db_session, o, "telegram", u)
    row = db_session.query(NotificationLog).one()
    assert row.channel == "telegram"
    assert row.recipient == "777"
    assert row.status == "sent"
    assert len(sent) == 1


def test_send_document_telegram_not_configured(db_session, env):
    c, o = env
    u = _user(db_session)
    with pytest.raises(ValueError, match="telegram"):
        send_document(db_session, o, "telegram", u)


def test_send_document_telegram_error_scrubs_token(db_session, env, monkeypatch):
    c, o = env
    set_setting(db_session, TELEGRAM_TOKEN_KEY, "123456:ABCDEF", user=None)
    set_setting(db_session, TELEGRAM_CHAT_KEY, "777", user=None)
    u = _user(db_session)

    def fake_fail(text, pdf, db=None, **kw):
        raise httpx.HTTPStatusError(
            'post "https://api.telegram.org/bot123456:ABCDEF/sendMessage"',
            request=httpx.Request("POST", "https://api.telegram.org/bot123456:ABCDEF/sendMessage"),
            response=httpx.Response(500),
        )

    monkeypatch.setattr("app.modules.notifications.telegram.send_telegram_document", fake_fail)
    send_document(db_session, o, "telegram", u)
    row = db_session.query(NotificationLog).one()
    assert row.status == "failed"
    assert "/bot***/" in row.error
    assert "123456:ABCDEF" not in row.error