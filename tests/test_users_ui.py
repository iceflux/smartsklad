from app.modules.auth.models import Role, User
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


def _users_manager(client, db_session, username="cmanager"):
    role = Role(name=username, is_system=False, is_super=False, permissions=["users.manage"])
    db_session.add(role)
    db_session.commit()
    u = User(username=username, password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)
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
    seller = _seller(db_session)
    r = client.get("/users")
    assert r.status_code == 200
    assert '/users/new' in r.text
    assert f'/users/{seller.id}/edit' in r.text


def test_users_page_reset_password_button_only_for_super(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    r = client.get("/users")
    assert r.status_code == 200
    assert f'onclick="adminReset({seller.id})"' in r.text
    assert "Сбросить пароль" in r.text

    _users_manager(client, db_session, "cmanager-row")
    r = client.get("/users")
    assert r.status_code == 200
    assert 'onclick="adminReset(' not in r.text
    assert "Сбросить пароль" not in r.text


def test_users_page_shows_success_message(client, db_session):
    _admin(client, db_session)
    r = client.get("/users?msg=Пользователь сохранён")
    assert r.status_code == 200
    assert "Пользователь сохранён" in r.text


def test_users_new_page_403_without_permission(client, db_session):
    _seller(db_session)
    s = login(db_session, "seller1", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.get("/users/new")
    assert r.status_code == 403


def test_users_edit_page_404_for_missing_user(client, db_session):
    _admin(client, db_session)
    r = client.get("/users/999999/edit")
    assert r.status_code == 404


def test_users_new_password_differs_between_loads(client, db_session):
    _admin(client, db_session)
    import re
    values = []
    for _ in range(2):
        r = client.get("/users/new")
        m = re.search(r'<input[^>]*name="password"[^>]*value="([^"]*)"', r.text)
        assert m and m.group(1)
        values.append(m.group(1))
    assert values[0] != values[1]


def test_users_new_page_does_not_persist_user(client, db_session):
    _admin(client, db_session)
    before = db_session.query(User).count()
    client.get("/users/new")
    assert db_session.query(User).count() == before


def test_edit_page_disables_super_controls_for_non_super(client, db_session):
    import re
    boss = _admin(client, db_session)
    _users_manager(client, db_session, "cmanager")
    r = client.get(f"/users/{boss.id}/edit")
    assert r.status_code == 200
    assert re.search(r'<select[^>]*name="role_id"[^>]*disabled', r.text)
    assert re.search(r'<input[^>]*name="is_active"[^>]*disabled', r.text)


def test_edit_page_enables_super_controls_for_super_actor(client, db_session):
    import re
    _admin(client, db_session)
    other = User(username="boss2", password_hash=hash_password(PASSWORD),
                 role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(other)
    db_session.commit()
    r = client.get(f"/users/{other.id}/edit")
    assert r.status_code == 200
    assert not re.search(r'<select[^>]*name="role_id"[^>]*disabled', r.text)
    assert not re.search(r'<input[^>]*name="is_active"[^>]*disabled', r.text)


def test_reset_password_button_only_for_super(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    r = client.get(f"/users/{seller.id}/edit")
    assert 'onclick="adminReset()"' in r.text
    _users_manager(client, db_session, "cmanager2")
    r = client.get(f"/users/{seller.id}/edit")
    assert 'onclick="adminReset()"' not in r.text


def test_users_new_page_shows_password_as_plain_text(client, db_session):
    import re
    _admin(client, db_session)
    r = client.get("/users/new")
    tag = re.search(r'<input[^>]*name="password"[^>]*>', r.text)
    assert tag
    assert 'type="password"' not in tag.group(0)
    assert re.search(r'value="[^"]+"', tag.group(0))


def test_users_edit_page_keeps_password_masked(client, db_session):
    import re
    _admin(client, db_session)
    seller = _seller(db_session)
    r = client.get(f"/users/{seller.id}/edit")
    tag = re.search(r'<input[^>]*name="password"[^>]*>', r.text)
    assert tag
    assert 'type="password"' in tag.group(0)
