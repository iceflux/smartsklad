from html.parser import HTMLParser

from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order
from app.modules.orders.service import create_order
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


def _seed(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=10)
    c = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add_all([p, c])
    db_session.commit()
    order = create_order(db_session, customer_id=c.id, discount=0,
                         items=[{"product_id": p.id, "quantity": 2, "discount": 0}])
    return p, c, order


class _FormParser(HTMLParser):
    """Собирает поля формы в порядке документа, минуя шаблон добавляемой строки.

    Раньше здесь разбирались `<select>`; теперь позицию несёт скрытое поле
    подстановки, поэтому разбор тот же по смыслу, но по `input` с именем.
    Содержимое `<template>` пропускается — иначе добавляемая строка считалась бы
    ещё одной позицией заказа."""

    def __init__(self):
        super().__init__()
        self.in_template = False
        self.fields = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "template":
            self.in_template = True
        elif not self.in_template and tag == "input" and attrs.get("name"):
            self.fields.append(attrs)

    def handle_endtag(self, tag):
        if tag == "template":
            self.in_template = False


def _parse_form(body):
    parser = _FormParser()
    parser.feed(body)
    return parser


def _posted_lines(body):
    columns = {"product_id": [], "quantity": [], "line_discount": []}
    for attrs in _parse_form(body).fields:
        if attrs["name"] in columns:
            columns[attrs["name"]].append(attrs.get("value", ""))
    return (columns["product_id"], columns["quantity"], columns["line_discount"])


def test_edit_form_saves_a_soft_deleted_line_beside_a_live_one(client, db_session):
    _admin(client, db_session)
    p, c, seed_order = _seed(db_session)
    live = Product(name="Доска", sku="BOARD-1", unit_id=_unit_id(db_session), price=50.0, rest=10)
    db_session.add(live)
    db_session.commit()
    order = create_order(db_session, customer_id=c.id, discount=0,
                         items=[{"product_id": p.id, "quantity": 2, "discount": 0},
                                {"product_id": live.id, "quantity": 5, "discount": 0}])
    order_id = order.id
    assert client.delete(f"/api/products/{p.id}").status_code == 204
    body = client.get(f"/orders/{order_id}/edit").text
    product_ids, quantities, discounts = _posted_lines(body)
    r = client.post(f"/orders/{order_id}/edit", data={
        "customer_id": str(c.id), "discount": "0",
        "product_id": product_ids, "quantity": quantities, "line_discount": discounts,
    }, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    saved = db_session.get(Order, order_id)
    assert [(i.product_id, i.quantity) for i in saved.items] == [(p.id, 2), (live.id, 5)]


def test_edit_form_save_keeps_an_imported_price_over_http(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    p.price = 0.0
    order.items[0].product_price_snapshot = 250.0
    order.items[0].unit_snapshot = "кг"
    db_session.commit()
    assert p.price == 0.0
    product_ids, quantities, discounts = _posted_lines(
        client.get(f"/orders/{order.id}/edit").text)
    r = client.post(f"/orders/{order.id}/edit", data={
        "customer_id": str(c.id), "discount": "0",
        "product_id": product_ids, "quantity": quantities, "line_discount": discounts,
    }, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    saved = db_session.get(Order, order.id)
    assert saved.items[0].product_price_snapshot == 250.0
    assert saved.items[0].unit_snapshot == "кг"
    assert saved.total_amount == 500.0


def test_edit_form_saves_an_order_of_a_soft_deleted_client(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    assert client.delete(f"/api/customers/{c.id}").status_code == 204
    product_ids, quantities, discounts = _posted_lines(
        client.get(f"/orders/{order.id}/edit").text)
    r = client.post(f"/orders/{order.id}/edit", data={
        "customer_id": str(c.id), "discount": "0",
        "product_id": product_ids, "quantity": quantities, "line_discount": discounts,
    }, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    assert db_session.get(Order, order.id).customer_id == c.id


def test_edit_form_still_404s_for_another_missing_client(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    assert client.delete(f"/api/customers/{c.id}").status_code == 204
    product_ids, quantities, discounts = _posted_lines(
        client.get(f"/orders/{order.id}/edit").text)
    r = client.post(f"/orders/{order.id}/edit", data={
        "customer_id": "9999", "discount": "0",
        "product_id": product_ids, "quantity": quantities, "line_discount": discounts,
    }, follow_redirects=False)
    assert r.status_code == 404
    db_session.expire_all()
    assert db_session.get(Order, order.id).customer_id == c.id


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


def test_copy_page_reports_a_missing_order_as_404(client, db_session):
    _admin(client, db_session)
    r = client.post("/orders/9999/copy", follow_redirects=False)
    assert r.status_code == 404


def test_copy_page_reports_a_deleted_client_as_404(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    assert client.delete(f"/api/customers/{c.id}").status_code == 204
    r = client.post(f"/orders/{order.id}/copy", follow_redirects=False)
    assert r.status_code == 404
    assert r.json()["detail"] == "Заказ не найден"


def test_copy_page_shows_the_stock_error_instead_of_404(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    db_session.expire_all()
    db_session.get(Product, p.id).rest = 1
    db_session.commit()
    r = client.post(f"/orders/{order.id}/copy", follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote_plus
    location = unquote_plus(r.headers["location"])
    assert location == (
        f"/orders/{order.id}?msg=не хватает остатка у товара Гвозди: нужно 2, есть 1")
    assert db_session.query(Order).count() == 1


def test_edit_page_still_404s_for_a_missing_order(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    r = client.post("/orders/9999/edit", data={
        "customer_id": str(c.id), "discount": "0",
        "product_id": [str(p.id)], "quantity": ["1"], "line_discount": ["0"],
    }, follow_redirects=False)
    assert r.status_code == 404
    assert "заказ не найден" in r.text


def test_edit_page_still_404s_in_russian_for_a_missing_client(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    r = client.post(f"/orders/{order.id}/edit", data={
        "customer_id": "9999", "discount": "0",
        "product_id": [str(p.id)], "quantity": ["1"], "line_discount": ["0"],
    }, follow_redirects=False)
    assert r.status_code == 404
    assert "клиент не найден" in r.text
    db_session.expire_all()
    assert db_session.get(Order, order.id).customer_id == c.id


def test_status_page_still_404s_for_a_missing_order(client, db_session):
    _admin(client, db_session)
    r = client.post("/orders/9999/status", data={"status": "processing"},
                    follow_redirects=False)
    assert r.status_code == 404
    assert r.json()["detail"] == "заказ не найден"


def test_status_page_shows_a_forbidden_transition_as_a_message(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    r = client.post(f"/orders/{order.id}/status", data={"status": "done"},
                    follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote_plus
    location = unquote_plus(r.headers["location"])
    assert location == f"/orders/{order.id}?msg=недопустимый переход статуса"


def test_new_page_shows_each_validation_error_in_russian(client, db_session):
    _admin(client, db_session)
    p, c, order = _seed(db_session)
    from urllib.parse import unquote_plus

    r = client.post("/orders/new", data={
        "customer_id": str(c.id), "discount": "0",
        "product_id": [str(p.id), str(p.id)],
        "quantity": ["1", "1"], "line_discount": ["0", "0"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert unquote_plus(r.headers["location"]) == "/orders/new?msg=товар в заказе повторяется"

    r = client.post("/orders/new", data={
        "customer_id": str(c.id), "discount": "0",
        "product_id": [str(p.id)], "quantity": ["0"], "line_discount": ["0"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert unquote_plus(r.headers["location"]) == (
        "/orders/new?msg=количество должно быть не меньше 1")

    r = client.post("/orders/new", data={
        "customer_id": str(c.id), "discount": "0",
        "product_id": [str(p.id)], "quantity": ["11"], "line_discount": ["0"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert unquote_plus(r.headers["location"]) == (
        "/orders/new?msg=не хватает остатка у товара Гвозди: нужно 11, есть 10")

    r = client.post("/orders/new", data={
        "customer_id": "9999", "discount": "0",
        "product_id": [str(p.id)], "quantity": ["1"], "line_discount": ["0"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert unquote_plus(r.headers["location"]) == "/orders/new?msg=клиент не найден"

    db_session.expire_all()
    assert db_session.query(Order).count() == 1


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