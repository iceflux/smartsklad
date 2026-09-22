from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.service import create_order

PASSWORD = "password123"


def _login(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _mk_order(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=100.0, rest=50)
    c = Customer(name="ООО Строй")
    db_session.add_all([p, c])
    db_session.commit()
    return create_order(db_session, customer_id=c.id, items=[{"product_id": p.id, "quantity": 2, "discount": 0}])


def test_print_page_requires_login(client, db_session):
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}/print", follow_redirects=False)
    assert r.status_code == 303
    assert "login" in r.headers.get("location", "")


def test_print_page_renders_for_viewer(client, db_session):
    _login(client, db_session, "viewer1", "seller")  # seller имеет orders.view
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}/print")
    assert r.status_code == 200
    assert "Заказ покупателя" in r.text
    assert o.number in r.text


def test_pdf_download(client, db_session):
    _login(client, db_session, "viewer2", "seller")
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.content.startswith(b"%PDF")


def test_pdf_missing_order_404(client, db_session):
    _login(client, db_session, "viewer3", "seller")
    r = client.get("/orders/999999/pdf")
    assert r.status_code == 404