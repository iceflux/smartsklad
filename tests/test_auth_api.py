from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.modules.auth import ratelimit
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login


def _seller(db_session, username="seller1"):
    u = User(username=username, password_hash=hash_password("password123"), role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    return u


def _fail_login(client, times=1, username="seller1"):
    return [
        client.post("/api/auth/login", json={"username": username, "password": "wrong"}).status_code
        for _ in range(times)
    ]


def _post_recovery(client, times=1, who="seller1"):
    return [
        client.post("/api/auth/password-recovery", json={"username_or_email": who}).status_code
        for _ in range(times)
    ]


def test_login_and_logout_via_api(client, db_session):
    _seller(db_session)

    r = client.post("/api/auth/login", json={"username": "seller1", "password": "password123"})
    assert r.status_code == 204
    assert r.cookies.get("session")

    r2 = client.post("/logout", follow_redirects=False)
    assert r2.status_code in (303, 307)
    assert r2.headers.get("location") == "/login"
    assert client.cookies.get("session") is None


def test_login_bad_credentials_returns_401(client, db_session):
    _seller(db_session)

    r = client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"})
    assert r.status_code == 401
    assert not r.cookies.get("session")


def test_login_gives_way_after_the_configured_number_of_failures(client, db_session):
    _seller(db_session)
    assert _fail_login(client, ratelimit.MAX_FAILURES) == [401] * ratelimit.MAX_FAILURES

    r = client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"})

    assert r.status_code == 429
    assert "Слишком много попыток входа" in r.json()["detail"]
    assert 0 < int(r.headers["Retry-After"]) <= ratelimit.WINDOW_SECONDS


def test_login_limit_is_per_address(client, db_session):
    _seller(db_session)
    _fail_login(client, ratelimit.MAX_FAILURES)
    assert client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"}).status_code == 429

    other = TestClient(client.app, client=("10.0.0.9", 40000))

    assert other.post("/api/auth/login", json={"username": "seller1", "password": "password123"}).status_code == 204
    assert client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"}).status_code == 429


def test_login_limit_expires(client, db_session, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    _seller(db_session)
    _fail_login(client, ratelimit.MAX_FAILURES)
    assert client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"}).status_code == 429

    clock[0] += ratelimit.WINDOW_SECONDS + 1

    r = client.post("/api/auth/login", json={"username": "seller1", "password": "password123"})
    assert r.status_code == 204


def test_successful_login_clears_failures(client, db_session):
    _seller(db_session)
    _fail_login(client, ratelimit.MAX_FAILURES - 1)
    assert client.post("/api/auth/login", json={"username": "seller1", "password": "password123"}).status_code == 204

    assert _fail_login(client, ratelimit.MAX_FAILURES) == [401] * ratelimit.MAX_FAILURES
    assert client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"}).status_code == 429


def test_password_recovery_is_rate_limited(client, db_session):
    assert _post_recovery(client, ratelimit.MAX_FAILURES) == [202] * ratelimit.MAX_FAILURES

    r = client.post("/api/auth/password-recovery", json={"username_or_email": "seller1"})

    assert r.status_code == 429
    assert "Слишком много запросов на восстановление пароля" in r.json()["detail"]
    assert 0 < int(r.headers["Retry-After"]) <= ratelimit.WINDOW_SECONDS


def test_password_recovery_limit_is_per_address(client, db_session):
    _post_recovery(client, ratelimit.MAX_FAILURES)
    assert client.post("/api/auth/password-recovery", json={"username_or_email": "seller1"}).status_code == 429

    other = TestClient(client.app, client=("10.0.0.9", 40000))

    assert other.post("/api/auth/password-recovery", json={"username_or_email": "seller1"}).status_code == 202
    assert client.post("/api/auth/password-recovery", json={"username_or_email": "seller1"}).status_code == 429


def test_recovery_requests_spend_the_login_budget(client, db_session):
    _seller(db_session)
    _fail_login(client, 6)
    assert _post_recovery(client, 4) == [202] * 4

    assert client.post("/api/auth/login", json={"username": "seller1", "password": "password123"}).status_code == 429
    assert client.post("/api/auth/password-recovery", json={"username_or_email": "seller1"}).status_code == 429


def test_rate_limit_covers_only_the_credential_endpoints(client, db_session):
    _seller(db_session)
    client.cookies.set("session", login(db_session, "seller1", "password123").token)
    _fail_login(client, ratelimit.MAX_FAILURES)
    assert client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"}).status_code == 429

    assert {client.get("/api/products").status_code for _ in range(ratelimit.MAX_FAILURES + 5)} == {200}
    assert {client.get("/login").status_code for _ in range(ratelimit.MAX_FAILURES + 5)} == {200}
