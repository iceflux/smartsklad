from fastapi.testclient import TestClient


def test_recovery_sends_email(client, db_session, monkeypatch):
    from app.modules.auth.models import User
    from app.modules.auth.service import get_role_by_name, hash_password

    sent = {}
    def fake_send(to, subject, body, **kwargs):
        sent["to"], sent["subject"], sent["body"] = to, subject, body

    monkeypatch.setattr("app.modules.auth.service.send_email", fake_send)
    u = User(username="seller1", password_hash=hash_password("password123"), role_id=get_role_by_name(db_session, "seller").id, email="s@s.ru")
    db_session.add(u)
    db_session.commit()

    r = client.post("/api/auth/password-recovery", json={"username_or_email": "seller1"})
    assert r.status_code == 202
    assert sent["to"] == "s@s.ru"
    assert "reset-password" in sent["body"]


def test_recovery_unknown_user_no_email(client, db_session, monkeypatch):
    called = []
    def fake_send(to, subject, body):
        called.append(to)

    monkeypatch.setattr("app.modules.auth.service.send_email", fake_send)
    r = client.post("/api/auth/password-recovery", json={"username_or_email": "nobody"})
    assert r.status_code == 202
    assert called == []


def test_reset_with_wrong_token(client, db_session):
    r = client.post("/api/auth/reset-password", json={"token": "bogus", "new_password": "password123"})
    assert r.status_code == 400


def test_admin_reset_requires_admin(client, db_session):
    from app.modules.auth.models import User
    from app.modules.auth.service import get_role_by_name, hash_password, login

    u = User(username="seller1", password_hash=hash_password("password123"), role_id=get_role_by_name(db_session, "seller").id, email="s@s.ru")
    db_session.add(u)
    a = User(username="boss", password_hash=hash_password("password123"), role_id=get_role_by_name(db_session, "admin").id, email="a@a.ru")
    db_session.add(a)
    db_session.commit()

    s = login(db_session, "seller1", "password123")
    client.cookies.set("session", s.token)
    r = client.post(f"/api/auth/users/{u.id}/admin-reset")
    assert r.status_code == 403

    s2 = login(db_session, "boss", "password123")
    client.cookies.set("session", s2.token)
    r = client.post(f"/api/auth/users/{u.id}/admin-reset")
    assert r.status_code == 200
    assert "temporary_password" in r.json()