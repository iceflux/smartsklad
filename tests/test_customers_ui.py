from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)


def _make_customer(client, **overrides):
    payload = {"name": "ООО Строй", "phone": "+79990001122"}
    payload.update(overrides)
    r = client.post("/api/customers", json=payload)
    assert r.status_code == 201
    return r.json()


def test_customers_page_redirects_anonymous(client):
    r = client.get("/customers", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_customers_page_forbidden_without_right(client, db_session):
    role = Role(name="nocust", is_system=False, is_super=False, permissions=["products.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username="nocust1", password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "nocust1", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/customers")
    assert r.status_code == 403


def test_customers_pages_render_for_admin(client, db_session):
    _admin(client, db_session)
    c = _make_customer(client, comment="постоянный")
    for url in ["/customers",
                "/customers/new",
                f"/customers/{c['id']}",
                f"/customers/{c['id']}/edit"]:
        r = client.get(url)
        assert r.status_code == 200, url
    r = client.get("/customers")
    assert "ООО Строй" in r.text
    assert "Клиенты" in r.text


def test_customer_detail_renders_fields(client, db_session):
    _admin(client, db_session)
    c = _make_customer(client, inn="7701234567", email="a@b.ru",
                       discount_card="C-001", personal_discount=5.0, comment="важный")
    r = client.get(f"/customers/{c['id']}")
    assert r.status_code == 200
    for text in ["ООО Строй", "7701234567", "a@b.ru", "C-001", "важный"]:
        assert text in r.text


def test_customer_form_submits_create(client, db_session):
    _admin(client, db_session)
    r = client.get("/customers/new")
    assert "Новый клиент" in r.text


def test_page_delete_and_restore(client, db_session):
    _admin(client, db_session)
    c = _make_customer(client)
    r = client.post(f"/customers/{c['id']}/delete", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/customers")
    assert "ООО Строй" not in r.text
    r = client.post(f"/customers/{c['id']}/restore", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/customers")
    assert "ООО Строй" in r.text


def test_customer_edit_empty_fields_no_none(client, db_session):
    _admin(client, db_session)
    c = _make_customer(client)
    r = client.get(f"/customers/{c['id']}/edit")
    assert r.status_code == 200
    assert 'value="None"' not in r.text
    assert '>None</textarea>' not in r.text