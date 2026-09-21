from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

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


def test_customers_denied_without_right(client, db_session):
    _login(client, db_session, "clerk", "warehouseman")
    r = client.get("/api/customers")
    assert r.status_code == 403


def test_customers_allowed_for_admin_seller_manager(client, db_session):
    for role in ("admin", "seller", "manager"):
        _login(client, db_session, f"u_{role}", role)
        r = client.get("/api/customers")
        assert r.status_code == 200, role


def test_create_and_get_customer(client, db_session):
    _admin(client, db_session)
    r = client.post("/api/customers", json={
        "name": "ООО Строй", "inn": "7701234567", "email": "a@b.ru",
        "discount_card": "C-001", "personal_discount": 5.0, "comment": "привет",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "ООО Строй"
    assert body["comment"] == "привет"
    cid = body["id"]
    r2 = client.get(f"/api/customers/{cid}")
    assert r2.status_code == 200
    assert r2.json()["personal_discount"] == 5.0


def test_create_customer_validation(client, db_session):
    _admin(client, db_session)
    assert client.post("/api/customers", json={"name": ""}).status_code == 422
    assert client.post("/api/customers", json={"name": "X", "inn": "123"}).status_code == 422
    assert client.post("/api/customers", json={"name": "X", "personal_discount": 150}).status_code == 422


def test_patch_customer(client, db_session):
    _admin(client, db_session)
    cid = client.post("/api/customers", json={"name": "A"}).json()["id"]
    r = client.patch(f"/api/customers/{cid}", json={"comment": "новый", "personal_discount": 10})
    assert r.status_code == 200
    assert r.json()["comment"] == "новый"
    r = client.patch("/api/customers/9999", json={"comment": "x"})
    assert r.status_code == 404


def test_patch_customer_null_discount_422(client, db_session):
    _admin(client, db_session)
    cid = client.post("/api/customers", json={"name": "A"}).json()["id"]
    r = client.patch(f"/api/customers/{cid}", json={"personal_discount": None})
    assert r.status_code == 422


def test_delete_and_restore_customer(client, db_session):
    _admin(client, db_session)
    cid = client.post("/api/customers", json={"name": "A"}).json()["id"]
    assert client.delete(f"/api/customers/{cid}").status_code == 204
    body = client.get(f"/api/customers/{cid}").json()
    assert body["is_deleted"] is True
    r = client.post(f"/api/customers/{cid}/restore")
    assert r.status_code == 200
    assert r.json()["is_deleted"] is False
    assert client.delete("/api/customers/9999").status_code == 404


def test_customers_list_search_pagination(client, db_session):
    _admin(client, db_session)
    client.post("/api/customers", json={"name": "ООО Строй", "phone": "+79990001122"})
    client.post("/api/customers", json={"name": "Иван", "phone": "+79991112233"})
    r = client.get("/api/customers")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    r2 = client.get("/api/customers", params={"search": "строй"})
    assert r2.json()["total"] == 1
    r3 = client.get("/api/customers", params={"page": 1, "page_size": 1})
    assert r3.json()["total"] == 2
    assert len(r3.json()["items"]) == 1