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
    p = Product(name="Гвозди", sku=f"SKU-API-{number}", unit_id=_unit_id(db_session), price=10.0, rest=3)
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


def _ordered_tops(db_session, count=6):
    for i in range(1, count + 1):
        c = Customer(name=f"Клиент-{i}", personal_discount=0.0)
        p = Product(name=f"Гвозди-{i}", sku=f"SKU-TOP-{i}", unit_id=_unit_id(db_session),
                    price=10.0, rest=50)
        db_session.add(c)
        db_session.add(p)
        db_session.commit()
        o = Order(number=f"{i:05d}", customer_id=c.id, status="done", discount=0.0,
                  total_amount=float(100 - i),
                  created_at=datetime.combine(date.today(), time(12, 0)))
        db_session.add(o)
        db_session.commit()
        o.items.append(OrderItem(product_id=p.id, product_name_snapshot=p.name,
                                 product_price_snapshot=10.0, discount_snapshot=0.0,
                                 quantity=i, unit_snapshot="шт"))
        db_session.commit()


def test_dashboard_api_top_limit_slices_both_tops(client, db_session):
    _admin(client, db_session)
    _ordered_tops(db_session)
    r = client.get("/api/dashboard/sales?preset=all&top_limit=2")
    assert r.status_code == 200
    body = r.json()
    assert [row["name"] for row in body["top_products"]] == ["Гвозди-6", "Гвозди-5"]
    assert [row["name"] for row in body["top_customers"]] == ["Клиент-1", "Клиент-2"]


def test_dashboard_api_top_limit_defaults_to_ten(client, db_session):
    _admin(client, db_session)
    _ordered_tops(db_session, count=12)
    body = client.get("/api/dashboard/sales?preset=all").json()
    assert [row["name"] for row in body["top_products"]] == [
        f"Гвозди-{i}" for i in (12, 11, 10, 9, 8, 7, 6, 5, 4, 3)]
    assert [row["name"] for row in body["top_customers"]] == [
        f"Клиент-{i}" for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]


def test_dashboard_api_top_limit_range(client, db_session):
    _admin(client, db_session)
    for bad in ("abc", "0", "501", "-1", ""):
        assert client.get(f"/api/dashboard/sales?top_limit={bad}").status_code == 422
    for good in ("1", "500"):
        assert client.get(f"/api/dashboard/sales?top_limit={good}").status_code == 200


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


def test_dashboard_csv_exports_resolved_period_and_rows(client, db_session):
    _admin(client, db_session)
    _seed(db_session, day=date(2026, 3, 3), amount=150.0, number="00001")
    r = client.get("/dashboard/export.csv?date_from=2026-03-01&date_to=2026-03-05")
    assert r.status_code == 200
    lines = r.text.replace("\ufeff", "", 1).splitlines()
    assert lines[0] == "date;orders;amount"
    assert "2026-03-01;0;0.0" in lines
    assert "2026-03-03;1;150.0" in lines
    assert "2026-03-05;0;0.0" in lines
    assert len(lines) == 6


def test_dashboard_api_all_preset_starts_at_the_first_order(client, db_session):
    _admin(client, db_session)
    _seed(db_session, day=date(2024, 3, 1), amount=50.0, number="00001")
    _seed(db_session, day=date.today(), amount=100.0, number="00002")
    r = client.get("/api/dashboard/sales?preset=all")
    assert r.status_code == 200
    body = r.json()
    assert body["date_from"] == date(2024, 3, 1).isoformat()
    assert body["date_to"] == date.today().isoformat()
    assert body["orders_count"] == 2
    assert body["total_sum"] == 150.0


def test_dashboard_api_all_preset_without_orders_is_today(client, db_session):
    _admin(client, db_session)
    r = client.get("/api/dashboard/sales?preset=all")
    assert r.status_code == 200
    assert r.json()["date_from"] == date.today().isoformat()
    assert r.json()["orders_count"] == 0


def test_dashboard_csv_all_preset_matches_the_api_period(client, db_session):
    _admin(client, db_session)
    _seed(db_session, day=date(2024, 3, 1), amount=50.0, number="00001")
    api = client.get("/api/dashboard/sales?preset=all").json()
    r = client.get("/dashboard/export.csv?preset=all")
    assert r.status_code == 200
    lines = r.text.replace("\ufeff", "", 1).splitlines()
    assert lines[1] == f"{api['date_from']};1;50.0"
    assert lines[-1] == f"{api['date_to']};0;0.0"
    assert len(lines) - 1 == len(api["daily"])


def test_dashboard_csv_quarter_preset_exports_the_calendar_quarter(client, db_session):
    _admin(client, db_session)
    today = date.today()
    quarter_start = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    _seed(db_session, day=quarter_start, amount=70.0, number="00001")
    api = client.get("/api/dashboard/sales?preset=quarter").json()
    assert api["date_from"] == quarter_start.isoformat()
    assert api["date_to"] == today.isoformat()
    assert api["orders_count"] == 1
    r = client.get("/dashboard/export.csv?preset=quarter")
    assert r.status_code == 200
    lines = r.text.replace("\ufeff", "", 1).splitlines()
    assert lines[1] == f"{quarter_start.isoformat()};1;70.0"
    assert len(lines) - 1 == len(api["daily"])
    tail = api["daily"][-1]
    assert lines[-1] == f"{tail['date']};{tail['orders']};{tail['amount']}"


def test_dashboard_empty_preset_matches_default_across_layers(client, db_session):
    _admin(client, db_session)
    default = client.get("/api/dashboard/sales").json()
    empty = client.get("/api/dashboard/sales?preset=").json()
    assert empty["date_from"] == default["date_from"]
    assert empty["date_to"] == default["date_to"]
    start = date.fromisoformat(default["date_from"])
    end = date.fromisoformat(default["date_to"])
    page = client.get("/dashboard?preset=")
    assert page.status_code == 200
    assert start.strftime("%d.%m.%Y") in page.text
    assert end.strftime("%d.%m.%Y") in page.text
    csv = client.get("/dashboard/export.csv?preset=")
    assert csv.status_code == 200
    lines = csv.text.replace("\ufeff", "", 1).splitlines()
    assert len(lines) == 31
    assert lines[1] == f"{start.isoformat()};0;0.0"
    assert lines[-1] == f"{end.isoformat()};0;0.0"
