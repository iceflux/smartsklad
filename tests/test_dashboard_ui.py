import re
from datetime import date, datetime, time, timedelta

from app.modules.auth.models import User
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


def _seed(db_session, amount=100.0, number="00001", day=None):
    c = Customer(name="ООО Строй", personal_discount=0.0)
    p = Product(name="Гвозди", sku=f"SKU-UI-{number}", unit_id=_unit_id(db_session), price=10.0, rest=3)
    db_session.add(c)
    db_session.add(p)
    db_session.commit()
    o = Order(number=number, customer_id=c.id, status="done", discount=0.0, total_amount=amount,
              created_at=datetime.combine(day or date.today(), time(12, 0)))
    db_session.add(o)
    db_session.commit()
    o.items.append(OrderItem(product_id=p.id, product_name_snapshot="Гвозди",
                             product_price_snapshot=10.0, discount_snapshot=0.0,
                             quantity=2, unit_snapshot="шт"))
    db_session.commit()
    return o


def _input_value(html, name):
    tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
    assert tag is not None, f"input {name} not found"
    value = re.search(r'value="([^"]*)"', tag.group(0))
    return value.group(1) if value else ""


def test_dashboard_page_redirects_anonymous(client, db_session):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


def test_dashboard_page_403_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "plain", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/dashboard")
    assert r.status_code == 403


def test_dashboard_page_renders_metrics_and_chart(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Аналитика продаж" in r.text
    assert "<svg" in r.text
    assert "Гвозди" in r.text
    assert "ООО Строй" in r.text
    assert "Средний чек" in r.text


def test_dashboard_page_empty_state(client, db_session):
    _admin(client, db_session)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "За выбранный период продаж нет" in r.text


def test_dashboard_page_shows_csv_link_for_manager(client, db_session):
    _admin(client, db_session)
    r = client.get("/dashboard")
    assert "/dashboard/export.csv" in r.text


def test_dashboard_csv_link_includes_resolved_dates(client, db_session):
    _admin(client, db_session)
    r = client.get("/dashboard?date_from=2026-03-01&date_to=2026-03-10")
    assert r.status_code == 200
    assert 'href="/dashboard/export.csv?date_from=2026-03-01&date_to=2026-03-10"' in r.text


def test_dashboard_page_preset_changes_period_and_keeps_dates_empty(client, db_session):
    _admin(client, db_session)
    today = date.today()
    r = client.get("/dashboard?preset=day")
    assert today.strftime("%d.%m.%Y") in r.text
    assert _input_value(r.text, "date_from") == ""
    assert _input_value(r.text, "date_to") == ""
    r = client.get("/dashboard?preset=year")
    assert (today - timedelta(days=364)).strftime("%d.%m.%Y") in r.text
    assert _input_value(r.text, "date_from") == ""
    assert _input_value(r.text, "date_to") == ""


def test_dashboard_page_offers_the_calendar_presets(client, db_session):
    _admin(client, db_session)
    r = client.get("/dashboard")
    assert r.status_code == 200
    for value, label in (
        ("quarter", "Текущий квартал"),
        ("prev_quarter", "Предыдущий квартал"),
        ("cal_year", "Год"),
        ("all", "Весь период"),
    ):
        assert f'value="{value}"' in r.text
        assert label in r.text
    assert 'value="year"' in r.text
    assert "365 дней" in r.text


def test_dashboard_page_all_preset_starts_at_the_first_order(client, db_session):
    _admin(client, db_session)
    _seed(db_session, number="00001", day=date(2025, 3, 1), amount=50.0)
    _seed(db_session, number="00002", amount=100.0)
    r = client.get("/dashboard?preset=all")
    assert r.status_code == 200
    assert date(2025, 3, 1).strftime("%d.%m.%Y") in r.text
    assert date.today().strftime("%d.%m.%Y") in r.text
    assert 'value="all" selected' in r.text
    assert "Заказов" in r.text


def test_dashboard_page_quarter_preset_starts_at_the_quarter_start(client, db_session):
    _admin(client, db_session)
    today = date.today()
    quarter_start = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    _seed(db_session, number="00001", day=quarter_start, amount=70.0)
    r = client.get("/dashboard?preset=quarter")
    assert r.status_code == 200
    assert quarter_start.strftime("%d.%m.%Y") in r.text
    assert 'value="quarter" selected' in r.text


def test_dashboard_page_hides_csv_link_without_export_permission(client, db_session):
    from app.modules.auth.models import Role
    role = Role(name="viewer", is_system=False, is_super=False, permissions=["analytics.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username="viewer", password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "viewer", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "/dashboard/export.csv" not in r.text


def test_nav_has_analytics_link_for_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/products")
    assert r.status_code == 200
    assert 'href="/dashboard"' in r.text


def test_nav_hides_analytics_link_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "plain", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/products")
    assert r.status_code == 200
    assert 'href="/dashboard"' not in r.text
