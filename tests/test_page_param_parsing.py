from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


PASSWORD = "password123"
WEIRD = "\u00b2"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)
    return u


def test_notifications_page_survives_malformed_page(client, db_session):
    _admin(client, db_session)
    for raw in (WEIRD, "abc", "-5", "0", "9" * 400):
        r = client.get(f"/notifications?page={raw}")
        assert r.status_code == 200


def test_products_page_survives_malformed_page(client, db_session):
    _admin(client, db_session)
    for raw in (WEIRD, "abc", "-5", "0", "9" * 400):
        r = client.get(f"/products?page={raw}")
        assert r.status_code == 200


def test_products_page_survives_malformed_category(client, db_session):
    _admin(client, db_session)
    r = client.get(f"/products?category_id={WEIRD}")
    assert r.status_code == 200


def test_customers_page_survives_malformed_page(client, db_session):
    _admin(client, db_session)
    r = client.get(f"/customers?page={WEIRD}")
    assert r.status_code == 200


def test_orders_page_survives_malformed_page(client, db_session):
    _admin(client, db_session)
    r = client.get(f"/orders?page={WEIRD}")
    assert r.status_code == 200


def test_orders_page_survives_malformed_customer(client, db_session):
    _admin(client, db_session)
    r = client.get(f"/orders?customer_id={WEIRD}")
    assert r.status_code == 200


def test_bulk_category_survives_malformed_ids(client, db_session):
    _admin(client, db_session)
    r = client.post("/products/bulk-category", data={
        "product_ids": [WEIRD, "abc", ""],
        "category_id": "1",
    }, follow_redirects=False)
    assert r.status_code == 303


def test_bulk_stock_survives_malformed_rest(client, db_session):
    _admin(client, db_session)
    p = Product(name="Гвозди", sku="SKU-PARSE-1", unit_id=_unit_id(db_session), price=10.0, rest=5)
    db_session.add(p)
    db_session.commit()
    r = client.post(f"/products/{p.id}/stock", data={"rest": WEIRD}, follow_redirects=False)
    assert r.status_code == 303
