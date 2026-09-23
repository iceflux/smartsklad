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


def _seller(db_session):
    u = User(
        username="seller1",
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, "seller").id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def test_users_new_page_redirects_when_anonymous(client, db_session):
    r = client.get("/users/new", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


def test_users_new_page_renders_for_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/users/new")
    assert r.status_code == 200
    assert "Создание пользователя" in r.text
    assert 'name="username"' in r.text
    assert 'name="email"' in r.text
    assert 'name="password"' in r.text
    assert 'name="role_id"' in r.text
    assert 'name="is_active"' in r.text


def test_users_new_page_has_generated_password(client, db_session):
    _admin(client, db_session)
    r = client.get("/users/new")
    import re
    m = re.search(r'<input[^>]*name="password"[^>]*value="([^"]*)"', r.text)
    assert m and m.group(1)


def test_users_edit_page_renders_for_admin(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    r = client.get(f"/users/{seller.id}/edit")
    assert r.status_code == 200
    assert "Редактирование пользователя" in r.text
    assert "seller1" in r.text


def test_users_page_shows_create_and_edit_links(client, db_session):
    _admin(client, db_session)
    _seller(db_session)
    r = client.get("/users")
    assert r.status_code == 200
    assert '/users/new' in r.text
    assert 'Редактировать' in r.text
