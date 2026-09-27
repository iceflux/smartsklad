from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.notifications.models import NotificationLog
from app.modules.orders.service import create_order
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


PASSWORD = "password123"


def _login(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


def _seller(client, db_session):
    _login(client, db_session, "seller1", "seller")


def _mk_order(db_session, email="a@b.ru"):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=50)
    c = Customer(name="ООО Строй", email=email)
    db_session.add_all([p, c])
    db_session.commit()
    return create_order(db_session, customer_id=c.id, items=[{"product_id": p.id, "quantity": 1, "discount": 0}])


def test_send_email_via_api(client, db_session, monkeypatch):
    _admin(client, db_session)
    o = _mk_order(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    r = client.post(f"/api/orders/{o.id}/send", json={"channel": "email"})
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    assert db_session.query(NotificationLog).count() == 1


def test_send_email_422_when_smtp_not_configured(client, db_session):
    _admin(client, db_session)
    o = _mk_order(db_session)
    r = client.post(f"/api/orders/{o.id}/send", json={"channel": "email"})
    assert r.status_code == 422
    assert "SMTP" in r.json()["detail"]
    assert db_session.query(NotificationLog).count() == 0


def test_send_forbidden_for_seller(client, db_session):
    _seller(client, db_session)
    o = _mk_order(db_session)
    r = client.post(f"/api/orders/{o.id}/send", json={"channel": "email"})
    assert r.status_code == 403


def test_send_bad_channel_422(client, db_session):
    _admin(client, db_session)
    o = _mk_order(db_session)
    r = client.post(f"/api/orders/{o.id}/send", json={"channel": "sms"})
    assert r.status_code == 422


def test_history_requires_right(client, db_session):
    _seller(client, db_session)
    r = client.get("/api/notifications")
    assert r.status_code == 403


def test_history_filters(client, db_session, monkeypatch):
    _admin(client, db_session)
    o = _mk_order(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    client.post(f"/api/orders/{o.id}/send", json={"channel": "email"})
    r = client.get("/api/notifications")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r2 = client.get("/api/notifications", params={"channel": "telegram"})
    assert r2.json()["total"] == 0