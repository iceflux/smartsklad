from app.modules.audit.models import AuditLog
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


def _seller(db_session, role_id=None):
    u = User(
        username="seller1",
        password_hash=hash_password(PASSWORD),
        role_id=role_id or get_role_by_name(db_session, "seller").id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def test_admin_lists_users(client, db_session):
    _admin(client, db_session)
    _seller(db_session)
    r = client.get("/api/users")
    assert r.status_code == 200
    usernames = {u["username"]: u for u in r.json()}
    assert "boss" in usernames
    assert usernames["seller1"]["role_name"] == "seller"


def test_admin_changes_user_role(client, db_session):
    admin = _admin(client, db_session)
    seller = _seller(db_session)
    manager = get_role_by_name(db_session, "manager")
    db_session.commit()
    r = client.patch(f"/api/users/{seller.id}/role", json={"role_id": manager.id})
    assert r.status_code == 200
    assert r.json()["role_id"] == manager.id
    db_session.expire_all()
    assert db_session.get(User, seller.id).role_id == manager.id
    entries = db_session.query(AuditLog).filter_by(action="user.role.change", entity_id=seller.id).all()
    assert len(entries) == 1
    assert entries[0].old_value == "seller"
    assert entries[0].new_value == "manager"
    assert entries[0].user_id == admin.id


def test_seller_cannot_change_own_role(client, db_session):
    seller = _seller(db_session)
    admin_role_id = get_role_by_name(db_session, "admin").id
    s = login(db_session, "seller1", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.patch(f"/api/users/{seller.id}/role", json={"role_id": admin_role_id})
    assert r.status_code == 403


def test_users_page_redirects_when_anonymous(client, db_session):
    r = client.get("/users", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


def test_users_page_renders_for_admin(client, db_session):
    _admin(client, db_session)
    _seller(db_session)
    r = client.get("/users")
    assert r.status_code == 200
    assert "Пользователи" in r.text
    assert "seller1" in r.text


def _custom_users_manager(client, db_session, username):
    role = Role(name=username, is_system=False, is_super=False,
                permissions=["users.manage"])
    db_session.add(role)
    db_session.commit()
    u = User(username=username, password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)
    return u


def test_admin_reset_requires_super(client, db_session):
    admin = _admin(client, db_session)
    seller = _seller(db_session)
    _custom_users_manager(client, db_session, "cmanager")
    r = client.post(f"/api/auth/users/{seller.id}/admin-reset")
    assert r.status_code == 403
    r = client.post(f"/api/auth/users/{admin.id}/admin-reset")
    assert r.status_code == 403


def test_super_admin_reset_returns_temporary_password_and_resets_super(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    r = client.post(f"/api/auth/users/{seller.id}/admin-reset")
    assert r.status_code == 200
    assert "temporary_password" in r.json()
    assert r.json()["temporary_password"]
    other_admin = User(
        username="boss2",
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, "admin").id,
    )
    db_session.add(other_admin)
    db_session.commit()
    r = client.post(f"/api/auth/users/{other_admin.id}/admin-reset")
    assert r.status_code == 200
    assert r.json()["temporary_password"]


def test_non_super_cannot_promote_to_super_role(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    _custom_users_manager(client, db_session, "cmanager4")
    r = client.patch(f"/api/users/{seller.id}/role",
                     json={"role_id": get_role_by_name(db_session, "admin").id})
    assert r.status_code == 403


def test_non_super_cannot_demote_or_touch_super_role(client, db_session):
    admin = _admin(client, db_session)
    _custom_users_manager(client, db_session, "cmanager5")
    r = client.patch(f"/api/users/{admin.id}/role",
                     json={"role_id": get_role_by_name(db_session, "seller").id})
    assert r.status_code == 403


def test_super_can_promote_user_to_super_role(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    admin_role_id = get_role_by_name(db_session, "admin").id
    r = client.patch(f"/api/users/{seller.id}/role", json={"role_id": admin_role_id})
    assert r.status_code == 200
    assert r.json()["role_id"] == admin_role_id


def test_non_super_can_reassign_regular_roles(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    _custom_users_manager(client, db_session, "cmanager6")
    manager_role_id = get_role_by_name(db_session, "manager").id
    r = client.patch(f"/api/users/{seller.id}/role", json={"role_id": manager_role_id})
    assert r.status_code == 200
    assert r.json()["role_id"] == manager_role_id


def test_admin_creates_user(client, db_session):
    _admin(client, db_session)
    role_id = get_role_by_name(db_session, "seller").id
    r = client.post("/api/users", json={
        "username": "newbie", "email": "new@x.ru", "password": "secret123", "role_id": role_id,
    })
    assert r.status_code == 201
    assert r.json()["username"] == "newbie"
    assert r.json()["is_active"] is True
    assert db_session.query(User).filter_by(username="newbie").first() is not None
    entries = db_session.query(AuditLog).filter_by(action="user.create").all()
    assert len(entries) == 1


def test_create_user_requires_permission(client, db_session):
    _seller(db_session)
    s = login(db_session, "seller1", PASSWORD)
    client.cookies.set("session", s.token)
    r = client.post("/api/users", json={
        "username": "x", "email": None, "password": "secret123",
        "role_id": get_role_by_name(db_session, "seller").id,
    })
    assert r.status_code == 403


def test_create_user_duplicate_returns_409(client, db_session):
    _admin(client, db_session)
    _seller(db_session)
    role_id = get_role_by_name(db_session, "seller").id
    r = client.post("/api/users", json={
        "username": "seller1", "email": None, "password": "secret123", "role_id": role_id,
    })
    assert r.status_code == 409
    assert "занят" in r.json()["detail"]


def test_create_user_short_password_returns_422(client, db_session):
    _admin(client, db_session)
    role_id = get_role_by_name(db_session, "seller").id
    r = client.post("/api/users", json={
        "username": "newbie", "email": None, "password": "short", "role_id": role_id,
    })
    assert r.status_code == 422


def test_patch_user_updates_fields(client, db_session):
    admin = _admin(client, db_session)
    seller = _seller(db_session)
    manager = get_role_by_name(db_session, "manager")
    r = client.patch(f"/api/users/{seller.id}", json={
        "username": "sellerX", "email": "x@y.ru", "role_id": manager.id,
        "is_active": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "sellerX"
    assert body["role_id"] == manager.id
    assert body["is_active"] is False
    db_session.expire_all()
    updated = db_session.get(User, seller.id)
    assert updated.username == "sellerX"
    assert updated.email == "x@y.ru"
    assert updated.role_id == manager.id
    assert updated.is_active is False
    db_session.refresh(admin)


def test_patch_preserves_password_when_not_sent(client, db_session):
    _admin(client, db_session)
    seller = _seller(db_session)
    old_hash = seller.password_hash
    r = client.patch(f"/api/users/{seller.id}", json={"username": "sellerX"})
    assert r.status_code == 200
    db_session.expire_all()
    updated = db_session.query(User).filter(User.id == seller.id).one()
    assert updated.password_hash == old_hash


def test_patch_deactivate_self_forbidden(client, db_session):
    _custom_users_manager(client, db_session, "cgmgr")
    me = db_session.query(User).filter_by(username="cgmgr").one()
    r = client.patch(f"/api/users/{me.id}", json={"is_active": False})
    assert r.status_code == 403


def test_patch_deactivate_last_super_forbidden(client, db_session):
    _custom_users_manager(client, db_session, "cgmgr2")
    admin = _admin(client, db_session)
    r = client.patch(f"/api/users/{admin.id}", json={"is_active": False})
    assert r.status_code == 403


def test_patch_user_not_found(client, db_session):
    _admin(client, db_session)
    r = client.patch("/api/users/999999", json={"username": "x"})
    assert r.status_code == 404


def test_patch_duplicate_username_returns_409(client, db_session):
    _admin(client, db_session)
    _seller(db_session)
    other = User(username="seller2", password_hash=hash_password(PASSWORD),
                 role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(other)
    db_session.commit()
    r = client.patch(f"/api/users/{other.id}", json={"username": "seller1"})
    assert r.status_code == 409