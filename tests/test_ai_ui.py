from datetime import date, datetime, time, timedelta

from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(
        username="boss", password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, "admin").id,
    )
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)
    return u


def _seed(db_session, rest=0, sku="SKU-UI"):
    c = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add(c)
    db_session.commit()
    p = Product(name="Гвозди", sku=sku, unit="шт", price=10.0, rest=rest)
    db_session.add(p)
    db_session.commit()
    first = date.today() - timedelta(days=19)
    for i in range(20):
        o = Order(number=f"{i + 1:05d}", customer_id=c.id, status="done", discount=0.0,
                  total_amount=0.0, created_at=datetime.combine(first + timedelta(days=i), time(12, 0)))
        db_session.add(o)
        db_session.commit()
        o.items.append(OrderItem(
            product_id=p.id, product_name_snapshot=p.name, product_price_snapshot=10.0,
            discount_snapshot=0.0, quantity=2, unit_snapshot="шт",
        ))
        db_session.commit()
    return p


def test_forecast_page_redirects_when_anonymous(client, db_session):
    r = client.get("/forecast", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


def test_forecast_page_403_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "plain", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/forecast")
    assert r.status_code == 403


def test_forecast_page_renders_rows(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    r = client.get("/forecast")
    assert r.status_code == 200
    assert "Прогноз спроса" in r.text
    assert "SKU-UI" in r.text
    assert "Гвозди" in r.text


def test_forecast_page_survives_malformed_page(client, db_session):
    _admin(client, db_session)
    for raw in ("\u00b2", "abc", "-5", "0", "9" * 400):
        r = client.get(f"/forecast?page={raw}")
        assert r.status_code == 200


def test_forecast_page_shows_little_data_marker(client, db_session):
    _admin(client, db_session)
    c = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add(c)
    db_session.commit()
    p = Product(name="Гвозди", sku="SKU-FEW", unit="шт", price=10.0, rest=0)
    db_session.add(p)
    db_session.commit()
    for i in range(3):
        o = Order(number=f"{i + 1:05d}", customer_id=c.id, status="done", discount=0.0, total_amount=0.0)
        db_session.add(o)
        db_session.commit()
        o.items.append(OrderItem(product_id=p.id, product_name_snapshot="Гвозди",
                                 product_price_snapshot=10.0, discount_snapshot=0.0,
                                 quantity=2, unit_snapshot="шт"))
        db_session.commit()
    r = client.get("/forecast")
    assert r.status_code == 200
    assert "мало данных" in r.text


def test_forecast_page_only_purchase_filter(client, db_session):
    _admin(client, db_session)
    _seed(db_session, rest=100000, sku="SKU-ENOUGH")
    r = client.get("/forecast?only_purchase=1")
    assert r.status_code == 200
    assert "SKU-ENOUGH" not in r.text


def test_forecast_page_shows_message(client, db_session):
    _admin(client, db_session)
    r = client.get("/forecast?msg=Готово")
    assert r.status_code == 200
    assert "Готово" in r.text


def test_nav_has_forecast_link_for_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/products")
    assert r.status_code == 200
    assert 'href="/forecast"' in r.text


def test_nav_hides_forecast_link_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "plain", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/products")
    assert r.status_code == 200
    assert 'href="/forecast"' not in r.text
