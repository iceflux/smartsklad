from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password


def test_login_and_logout_via_api(client, db_session):
    u = User(username="seller1", password_hash=hash_password("password123"), role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()

    r = client.post("/api/auth/login", json={"username": "seller1", "password": "password123"})
    assert r.status_code == 204
    assert r.cookies.get("session")

    r2 = client.post("/logout", follow_redirects=False)
    assert r2.status_code in (303, 307)
    assert r2.headers.get("location") == "/login"
    assert client.cookies.get("session") is None


def test_login_bad_credentials_returns_401(client, db_session):
    u = User(username="seller1", password_hash=hash_password("password123"), role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()

    r = client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"})
    assert r.status_code == 401
    assert not r.cookies.get("session")