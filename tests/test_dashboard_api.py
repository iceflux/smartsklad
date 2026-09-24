from datetime import date, datetime, time, timedelta

from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)
    return u


def _analyst(client, db_session, username="analyst"):
    role = Role(name=username, is_system=False, is_super=False, permissions=["analytics.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username=username, password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)
    return u


def _exporter(client, db_session, username="exporter"):
    role = Role(name=username, is_system=False, is_super=False, permissions=["reports.export"])
    db_session.add(role)
    db_session.commit()
    u = User(username=username, password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)
    return u


def _seed(db_session, day=None, amount=100.0, number="00001"):
    day = day or date.today()
    c = Customer(name="ООО Строй", personal_discount=0.0)
    p = Product(name="Гвозди", sku=f"SKU-API-{number}", unit="шт", price=10.0, rest=3)
    db_session.add(c)
    db_session.add(p)
    db_session.commit()
    o = Order(number=number, customer_id=c.id, status="done", discount=0.0, total_amount=amount,
              created_at=datetime.combine(day, time(12, 0)))
    db_session.add(o)
    db_session.commit()
    o.items.append(OrderItem(product_id=p.id, product_name_snapshot="Гвозди",
                             product_price_snapshot=10.0, discount_snapshot=0.0,
                             quantity=2, unit_snapshot="шт"))
    db_session.commit()
    return o


def test_dashboard_api_requires_auth(client, db_session):
    r = client.get("/api/dashboard/sales")
    assert r.status_code == 401


def test_dashboard_api_403_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "plain", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/api/dashboard/sales")
    assert r.status_code == 403


def test_dashboard_api_returns_metrics_for_analyst(client, db_session):
    _analyst(client, db_session)
    _seed(db_session)
    r = client.get("/api/dashboard/sales")
    assert r.status_code == 200
    body = r.json()
    for key in ("orders_count", "total_sum", "avg_check", "daily",
                "top_products", "top_customers", "date_from", "date_to"):
        assert key in body
    assert "bars" not in body


def test_dashboard_api_inverted_period_returns_422(client, db_session):
    _admin(client, db_session)
    r = client.get("/api/dashboard/sales?date_from=2026-02-01&date_to=2026-01-01")
    assert r.status_code == 422


def test_dashboard_api_garbage_period_does_not_500(client, db_session):
    _admin(client, db_session)
    today = date.today()
    month_start = today - timedelta(days=29)
    r = client.get("/api/dashboard/sales?preset=nonsense")
    assert r.status_code == 200
    assert r.json()["date_from"] == today.isoformat()
    assert r.json()["date_to"] == today.isoformat()
    for query in ("date_from=не%20дата", "date_to=abc"):
        r = client.get(f"/api/dashboard/sales?{query}")
        assert r.status_code == 200
        assert r.json()["date_from"] == month_start.isoformat()
        assert r.json()["date_to"] == today.isoformat()


def test_dashboard_csv_requires_permission(client, db_session):
    _analyst(client, db_session)
    r = client.get("/dashboard/export.csv")
    assert r.status_code == 403


def test_dashboard_csv_for_reports_exporter_only(client, db_session):
    _exporter(client, db_session)
    _seed(db_session)
    r = client.get("/dashboard/export.csv")
    assert r.status_code == 200
    assert client.get("/api/dashboard/sales").status_code == 403


def test_dashboard_csv_anonymous_redirects_to_login(client, db_session):
    r = client.get("/dashboard/export.csv", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


def test_dashboard_csv_inverted_period_returns_422(client, db_session):
    _admin(client, db_session)
    r = client.get("/dashboard/export.csv?date_from=2026-02-01&date_to=2026-01-01")
    assert r.status_code == 422


def test_dashboard_csv_for_manager(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    r = client.get("/dashboard/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="dashboard.csv"'
    text = r.text.replace("\ufeff", "", 1)
    assert text.splitlines()[0] == "date;orders;amount"
