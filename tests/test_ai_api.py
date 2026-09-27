from datetime import date, datetime, time, timedelta

from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


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


def _analyst(client, db_session):
    role = Role(name="analyst", is_system=False, is_super=False, permissions=["analytics.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username="analyst", password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "analyst", PASSWORD)
    client.cookies.set("session", s.token)
    return u


def _seed(db_session, rest=0, sku="SKU-API", first_number=1):
    c = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add(c)
    db_session.commit()
    p = Product(name="Гвозди", sku=sku, unit_id=_unit_id(db_session), price=10.0, rest=rest)
    db_session.add(p)
    db_session.commit()
    first = date.today() - timedelta(days=19)
    for i in range(20):
        o = Order(number=f"{first_number + i:05d}", customer_id=c.id, status="done", discount=0.0,
                  total_amount=0.0, created_at=datetime.combine(first + timedelta(days=i), time(12, 0)))
        db_session.add(o)
        db_session.commit()
        o.items.append(OrderItem(
            product_id=p.id, product_name_snapshot=p.name, product_price_snapshot=10.0,
            discount_snapshot=0.0, quantity=2, unit_snapshot="шт",
        ))
        db_session.commit()
    return p


def test_forecast_api_requires_auth(client, db_session):
    r = client.get("/api/forecast")
    assert r.status_code == 401


def test_forecast_api_allowed_for_non_super_analyst(client, db_session):
    _analyst(client, db_session)
    _seed(db_session)
    r = client.get("/api/forecast")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_forecast_api_403_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "plain", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/api/forecast")
    assert r.status_code == 403


def test_forecast_api_returns_rows(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    r = client.get("/api/forecast")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert row["sku"] == "SKU-API"
    assert row["has_data"] is True
    assert row["forecast_7"] is not None
    for key in ("product_id", "sku", "name", "unit", "rest", "min_stock", "avg_daily_sold",
                "days_with_sales", "has_data", "forecast_7", "forecast_14", "forecast_30",
                "recommended_qty"):
        assert key in row


def test_forecast_api_only_purchase_filter(client, db_session):
    _admin(client, db_session)
    _seed(db_session, rest=100000)
    r = client.get("/api/forecast?only_purchase=true")
    assert r.status_code == 200
    assert r.json() == []


def test_forecast_api_only_purchase_returns_only_qualifying_rows(client, db_session):
    _admin(client, db_session)
    _seed(db_session, rest=0, sku="SKU-NEED", first_number=1)
    for i in range(3):
        _seed(db_session, rest=100000, sku=f"SKU-ENOUGH-{i}", first_number=100 + i * 20)
    r = client.get("/api/forecast?only_purchase=true&page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["sku"] == "SKU-NEED"
    assert body[0]["recommended_qty"] is not None


def test_forecast_api_rejects_out_of_range_pagination(client, db_session):
    _admin(client, db_session)
    for query in ("page=0", "page=-1", "page_size=0", "page_size=101"):
        r = client.get(f"/api/forecast?{query}")
        assert r.status_code == 422


def test_forecast_api_accepts_pagination_boundaries(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    r = client.get("/api/forecast?page=1&page_size=1")
    assert r.status_code == 200
    r = client.get("/api/forecast?page_size=100")
    assert r.status_code == 200
