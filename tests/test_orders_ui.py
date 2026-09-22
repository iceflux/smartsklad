from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order
from app.modules.orders.service import create_order

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)


def _seed(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=100.0, rest=10)
    c = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add_all([p, c])
    db_session.commit()
    order = create_order(db_session, customer_id=c.id, discount=0,
                         items=[{"product_id": p.id, "quantity": 2, "discount": 0}])
    return p, c, order


def test_orders_page_redirects_anonymous(client):
    r = client.get("/orders", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_orders_page_forbidden_without_right(client, db_session):
    role = Role(name="noorders", is_system=False, is_super=False, permissions=["products.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username="clerk", password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "clerk", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/orders")
    assert r.status_code == 403


def test_orders_pages_render(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    for url in ["/orders", "/orders/new"]:
        r = client.get(url)
        assert r.status_code == 200, url
    assert client.get(f"/orders/{order.id}").status_code == 200
    assert client.get(f"/orders/{order.id}/edit").status_code == 200
    r = client.get("/orders")
    assert "ООО Строй" in r.text
    assert "00001" in r.text


def test_orders_ui_actions(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)

    r = client.post(f"/orders/{order.id}/status", data={"status": "processing"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/orders/{order.id}/status", data={"status": "done"},
                    follow_redirects=False)
    assert r.status_code == 303
    db_session.refresh(p)
    assert p.rest == 8

    r = client.get(f"/orders/{order.id}")
    assert r.status_code == 200
    assert "Гвозди" in r.text

    before = db_session.query(Order).count()
    r = client.post(f"/orders/{order.id}/copy", follow_redirects=False)
    assert r.status_code == 303
    assert db_session.query(Order).count() == before + 1

    r = client.post(f"/orders/{order.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/orders")
    assert "00001" not in r.text
    assert "00002" in r.text

    r = client.post(f"/orders/{order.id}/restore", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/orders")
    assert "00001" in r.text


def test_orders_new_form_submit_creates(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    before = db_session.query(Order).count()
    r = client.post("/orders/new", data={
        "customer_id": str(c.id),
        "discount": "0",
        "product_id": [str(p.id)],
        "quantity": ["3"],
        "line_discount": ["0"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/orders/")
    assert db_session.query(Order).count() == before + 1
    r = client.get(r.headers["location"])
    assert r.status_code == 200
    assert "Гвозди" in r.text


def test_orders_new_form_bad_number_no_500(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    r = client.post("/orders/new", data={
        "customer_id": str(c.id),
        "discount": "abc",
        "product_id": [str(p.id)],
        "quantity": ["qwerty"],
        "line_discount": ["xyz"],
    }, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote_plus
    assert "Добавьте хотя бы одну позицию" in unquote_plus(r.headers["location"])


def test_order_detail_shows_history(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    r = client.get(f"/orders/{order.id}")
    assert r.status_code == 200
    assert "История изменений" in r.text
    assert "order.create" in r.text


def test_orders_edit_done_order_redirects(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    client.post(f"/orders/{order.id}/status", data={"status": "processing"},
                follow_redirects=False)
    client.post(f"/orders/{order.id}/status", data={"status": "done"},
                follow_redirects=False)
    r = client.get(f"/orders/{order.id}/edit", follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote_plus
    assert "Нельзя редактировать" in unquote_plus(r.headers["location"])