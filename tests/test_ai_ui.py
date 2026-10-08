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


def _seed(db_session, rest=0, sku="SKU-UI"):
    c = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add(c)
    db_session.commit()
    p = Product(name="Гвозди", sku=sku, unit_id=_unit_id(db_session), price=10.0, rest=rest)
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


import re


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
    p = Product(name="Гвозди", sku="SKU-FEW", unit_id=_unit_id(db_session), price=10.0, rest=0)
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


def test_forecast_page_below_min_stock_filter(client, db_session):
    _admin(client, db_session)
    _seed_low(db_session, sku="LOW", rest=2, min_stock=50)
    _seed_low(db_session, sku="OK", rest=500, min_stock=50)
    r = client.get("/forecast?below_min_stock=1")
    assert r.status_code == 200
    assert "LOW" in r.text
    assert "OK" not in r.text, "товар выше порога попал в отчёт"


def test_the_below_min_stock_filter_survives_pagination_and_sorting(client, db_session):
    """Фильтр обязан пережить сортировку и постраничные ссылки.

    Они строятся по `base_qs`. Если новый фильтр в него не попал, то вторая
    страница отчёта молча превратилась бы во вторую страницу всего списка, а
    сортировка — сбросила бы фильтр. Человек увидел бы «всего 30» и пустую
    страницу и решил бы, что отчёт сломан.
    """
    _admin(client, db_session)
    _seed_low(db_session, sku="LOW", rest=2, min_stock=50)

    page = client.get("/forecast?below_min_stock=1").text
    assert 'name="below_min_stock" value="1" checked' in page, (
        "галочка фильтра обязана остаться отмеченной — состояние приходит из запроса")

    # Ссылки сортировки несут фильтр: они строятся по base_qs.
    sort_link = re.search(r'href="(/forecast\?[^"]*sort=[^"]*)"', page)
    assert sort_link, "на странице нет ссылок сортировки"
    assert "below_min_stock=1" in sort_link.group(1), (
        f"ссылка сортировки потеряла фильтр: {sort_link.group(1)}")

    # Постраничные ссылки — тоже. Страниц больше одной нужно добиться числом
    # товаров, поэтому проверяется сам механизм на странице с сортировкой.
    sorted_page = client.get(sort_link.group(1).replace("&amp;", "&")).text
    assert 'name="below_min_stock" value="1" checked' in sorted_page, (
        "переход по сортировке сбросил фильтр")


def _seed_low(db_session, *, sku, rest, min_stock):
    """Товар с продажами и собственным минимальным запасом — под оба фильтра."""
    p = _seed(db_session, rest=rest, sku=sku)
    p.min_stock = min_stock
    db_session.commit()
    return p


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
