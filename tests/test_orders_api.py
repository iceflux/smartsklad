import pytest

from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory.models import Product
from app.modules.customers.models import Customer
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


PASSWORD = "password123"


def _login(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


@pytest.fixture
def env(client, db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=50)
    c = Customer(name="ООО Строй")
    db_session.add_all([p, c])
    db_session.commit()
    return p, c


def test_api_create_and_get_order(client, db_session, env):
    _admin(client, db_session)
    p, c = env
    r = client.post("/api/orders", json={
        "customer_id": c.id,
        "discount": 0,
        "items": [{"product_id": p.id, "quantity": 2, "discount": 0}],
    })
    assert r.status_code == 201
    data = r.json()
    assert data["number"] == "00001"
    assert data["total_amount"] == 200.0
    r2 = client.get(f"/api/orders/{data['id']}")
    assert r2.status_code == 200
    assert r2.json()["items"][0]["product_name_snapshot"] == "Гвозди"


def test_api_order_status_flow(client, db_session, env):
    _admin(client, db_session)
    p, c = env
    r = client.post("/api/orders", json={
        "customer_id": c.id, "items": [{"product_id": p.id, "quantity": 2, "discount": 0}],
    })
    oid = r.json()["id"]
    r = client.post(f"/api/orders/{oid}/status", json={"status": "processing"})
    assert r.status_code == 200
    r = client.post(f"/api/orders/{oid}/status", json={"status": "done"})
    assert r.json()["status"] == "done"
    assert client.get("/api/products/1").json()["rest"] == 48
    r = client.post(f"/api/orders/{oid}/copy")
    assert r.status_code == 201
    assert r.json()["number"] == "00002"


def test_api_order_denied_without_right(client, db_session, env):
    role = Role(name="noorders", is_system=False, is_super=False,
                permissions=["products.view"])
    db_session.add(role)
    db_session.commit()
    _login(client, db_session, "clerk", "noorders")
    assert client.get("/api/orders").status_code == 403
    assert client.post("/api/orders", json={
        "customer_id": 1, "items": [{"product_id": 1, "quantity": 1, "discount": 0}],
    }).status_code == 403


def test_api_reports_a_missing_order_as_404_in_russian(client, db_session, env):
    _admin(client, db_session)
    r = client.patch("/api/orders/9999", json={"discount": 0})
    assert r.status_code == 404
    assert r.json()["detail"] == "заказ не найден"


def test_api_reports_a_missing_client_as_404_in_russian(client, db_session, env):
    _admin(client, db_session)
    p, _c = env
    r = client.post("/api/orders", json={
        "customer_id": 9999, "items": [{"product_id": p.id, "quantity": 1, "discount": 0}],
    })
    assert r.status_code == 404
    assert r.json()["detail"] == "клиент не найден"


def test_api_reports_a_validation_error_as_422_with_russian_text(client, db_session, env):
    _admin(client, db_session)
    p, c = env
    line = {"product_id": p.id, "quantity": 1, "discount": 0}

    repeated = client.post("/api/orders", json={
        "customer_id": c.id, "items": [line, dict(line)]})
    assert repeated.status_code == 422
    assert repeated.json()["detail"] == "товар в заказе повторяется"

    over = client.post("/api/orders", json={
        "customer_id": c.id, "items": [dict(line, quantity=51)]})
    assert over.status_code == 422
    assert over.json()["detail"] == "не хватает остатка у товара Гвозди: нужно 51, есть 50"

    below_one = client.post("/api/orders", json={
        "customer_id": c.id, "items": [dict(line, quantity=0)]})
    assert below_one.status_code == 422
    assert below_one.json()["detail"] == "количество должно быть не меньше 1"

    oid = client.post("/api/orders", json={
        "customer_id": c.id, "items": [line]}).json()["id"]
    empty = client.patch(f"/api/orders/{oid}", json={"items": []})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "добавьте хотя бы одну позицию"