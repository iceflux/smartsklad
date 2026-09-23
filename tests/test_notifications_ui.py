from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.notifications.models import NotificationLog
from app.modules.orders.service import create_order

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


def _mk_order(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=100.0, rest=50)
    c = Customer(name="ООО Строй", email="a@b.ru")
    db_session.add_all([p, c])
    db_session.commit()
    return create_order(db_session, customer_id=c.id, items=[{"product_id": p.id, "quantity": 1, "discount": 0}])


def test_print_page_shows_send_buttons_for_admin(client, db_session):
    _admin(client, db_session)
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}/print")
    assert r.status_code == 200
    assert "На email" in r.text
    assert "В Telegram" in r.text


def test_print_page_hides_send_buttons_for_seller(client, db_session):
    _seller(client, db_session)
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}/print")
    assert r.status_code == 200
    assert "На email" not in r.text


def test_send_page_email_redirect(client, db_session, monkeypatch):
    _admin(client, db_session)
    o = _mk_order(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    r = client.post(f"/orders/{o.id}/send", data={"channel": "email"}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote_plus
    assert "Отправлено" in unquote_plus(r.headers["location"])
    assert db_session.query(NotificationLog).count() == 1


def test_history_page_shows_rows(client, db_session, monkeypatch):
    _admin(client, db_session)
    o = _mk_order(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    client.post(f"/orders/{o.id}/send", data={"channel": "email"})
    r = client.get("/notifications")
    assert r.status_code == 200
    assert "email" in r.text
    assert "a@b.ru" in r.text


def test_history_page_403_for_seller(client, db_session):
    _seller(client, db_session)
    r = client.get("/notifications")
    assert r.status_code == 403