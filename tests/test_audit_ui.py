from datetime import datetime

from app.modules.audit.models import AuditLog
from app.modules.audit.service import log_action
from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


def _ensure_user(db_session, username, role):
    u = db_session.query(User).filter(User.username == username).first()
    if u is None:
        u = User(
            username=username,
            password_hash=hash_password(PASSWORD),
            role_id=get_role_by_name(db_session, role).id,
        )
        db_session.add(u)
        db_session.commit()
    return u


def _login(client, db_session, username, role):
    _ensure_user(db_session, username, role)
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


def test_audit_page_redirects_anonymous(client):
    r = client.get("/audit", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_audit_page_forbidden_without_audit_view(client, db_session):
    role = Role(name="noaudit", is_system=False, is_super=False, permissions=["products.view"])
    db_session.add(role)
    db_session.commit()
    _login(client, db_session, "noaudit1", "noaudit")
    r = client.get("/audit")
    assert r.status_code == 403


def test_audit_page_renders_logs_for_admin(client, db_session):
    _admin(client, db_session)
    u = _ensure_user(db_session, "boss", "admin")
    log_action(
        db_session, action="products.create", entity_type="product", entity_id=1,
        new_value='{"name": "Хлеб"}', user_id=u.id, ip="127.0.0.1",
    )
    r = client.get("/audit")
    assert r.status_code == 200
    assert "Журнал аудита" in r.text
    assert "products.create" in r.text
    assert "Хлеб" in r.text


def test_audit_filter_by_action(client, db_session):
    _admin(client, db_session)
    u = _ensure_user(db_session, "boss", "admin")
    log_action(db_session, action="products.create", entity_type="product", user_id=u.id)
    log_action(db_session, action="categories.create", entity_type="category", user_id=u.id)
    r = client.get("/audit", params={"action": "products.create"})
    assert r.status_code == 200
    assert "products.create" in r.text
    assert "categories.create" not in r.text


def test_audit_valid_date_filter(client, db_session):
    _admin(client, db_session)
    u = _ensure_user(db_session, "boss", "admin")
    log_action(db_session, action="products.create", entity_type="product", user_id=u.id)
    r = client.get("/audit", params={"from_": "2020-01-01"})
    assert r.status_code == 200
    assert "products.create" in r.text
    r = client.get("/audit", params={"to_": "2020-01-01"})
    assert r.status_code == 200
    assert "products.create" not in r.text


def test_audit_invalid_date_filter_returns_400(client, db_session):
    _admin(client, db_session)
    r = client.get("/audit", params={"from_": "not-a-date"})
    assert r.status_code == 400


def test_audit_pagination_page_size(client, db_session):
    _admin(client, db_session)
    u = _ensure_user(db_session, "boss", "admin")
    for i in range(3):
        db_session.add(AuditLog(
            action=f"log.{i}", entity_type="product", user_id=u.id,
            created_at=datetime(2026, 9, i + 1, 12, 0),
        ))
    db_session.commit()
    r = client.get("/audit", params={"page_size": 2, "page": 1})
    assert r.status_code == 200
    assert "log.2" in r.text
    assert "log.0" not in r.text
    r = client.get("/audit", params={"page_size": 2, "page": 2})
    assert r.status_code == 200
    assert "log.0" in r.text
    assert "log.2" not in r.text