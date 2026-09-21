from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(
        username="boss",
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, "admin").id,
    )
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)
    return u


def test_get_roles_lists_seeded_roles(client, db_session):
    _admin(client, db_session)
    r = client.get("/api/roles")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 4
    admin = next(x for x in body if x["name"] == "admin")
    assert admin["is_super"] is True
    assert admin["is_system"] is True


def test_roles_denied_for_seller(client, db_session):
    u = User(username="seller1", password_hash=hash_password(PASSWORD), role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "seller1", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/api/roles")
    assert r.status_code == 403


def test_create_role(client, db_session):
    _admin(client, db_session)
    r = client.post("/api/roles", json={"name": "Кассир", "permissions": ["products.view"]})
    assert r.status_code == 201
    assert r.json()["name"] == "Кассир"


def test_duplicate_role_name_conflict(client, db_session):
    _admin(client, db_session)
    client.post("/api/roles", json={"name": "Кассир", "permissions": []})
    r = client.post("/api/roles", json={"name": "Кассир", "permissions": []})
    assert r.status_code == 409


def test_patch_permissions_updates_role(client, db_session):
    _admin(client, db_session)
    role_id = client.post("/api/roles", json={"name": "Кассир", "permissions": ["products.view"]}).json()["id"]
    r = client.patch(f"/api/roles/{role_id}", json={"name": "Кассир", "permissions": ["products.view", "orders.view"]})
    assert r.status_code == 200
    assert r.json()["permissions"] == ["orders.view", "products.view"]


def test_patch_rename_system_role_conflict(client, db_session):
    _admin(client, db_session)
    seller = get_role_by_name(db_session, "seller")
    r = client.patch(f"/api/roles/{seller.id}", json={"name": "Продавец2", "permissions": seller.permissions})
    assert r.status_code == 409


def test_delete_custom_role(client, db_session):
    _admin(client, db_session)
    role_id = client.post("/api/roles", json={"name": "Кассир", "permissions": []}).json()["id"]
    r = client.delete(f"/api/roles/{role_id}")
    assert r.status_code == 204


def test_delete_system_role_conflict(client, db_session):
    _admin(client, db_session)
    seller = get_role_by_name(db_session, "seller")
    r = client.delete(f"/api/roles/{seller.id}")
    assert r.status_code == 409


def test_delete_role_in_use_conflict(client, db_session):
    _admin(client, db_session)
    role_id = client.post("/api/roles", json={"name": "Кассир", "permissions": []}).json()["id"]
    u = User(username="cashier1", password_hash="x", role_id=role_id)
    db_session.add(u)
    db_session.commit()
    r = client.delete(f"/api/roles/{role_id}")
    assert r.status_code == 409


def test_roles_page_redirects_when_anonymous(client, db_session):
    r = client.get("/roles", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


def test_roles_page_renders_for_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/roles")
    assert r.status_code == 200
    assert "Роли" in r.text
    assert "boss" in r.text