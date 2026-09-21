from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


def _login_as(client, db_session, username, role):
    u = User(
        username=username,
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, role).id,
    )
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)
    return u


def test_index_redirects_anonymous_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_index_renders_for_admin(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Выйти" in body
    assert "boss" in body
    assert "/products" in body
    assert "/categories" in body
    assert "Каркас проекта готов" not in body


def test_index_shows_customers_link_by_permission(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    r = client.get("/")
    assert "/customers" in r.text
    _login_as(client, db_session, "handy", "warehouseman")
    r = client.get("/")
    assert "/customers" not in r.text


def test_index_shows_products_links_for_seller(client, db_session):
    _login_as(client, db_session, "sailor", "seller")
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "/products" in body
    assert "/roles" not in body
    assert "/audit" not in body