import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.settings.service import set_setting

PASSWORD = "password123"


def _login(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


def test_get_settings_requires_auth(client):
    r = client.get("/api/settings")
    assert r.status_code == 401


def test_get_settings(client, db_session):
    _admin(client, db_session)
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert any(s["key"] == "stock_track_default" and s["value"] == "true" for s in r.json())


def test_put_settings_requires_permission(client, db_session):
    _login(client, db_session, "seller1", "seller")  # seller не имеет settings.change
    r = client.put("/api/settings/stock-track", json={"value": "false"})
    assert r.status_code == 403


def test_put_settings_updates(client, db_session):
    _admin(client, db_session)
    r = client.put("/api/settings/stock-track", json={"value": "false"})
    assert r.status_code == 200
    assert r.json()["value"] == "false"


def test_put_settings_invalid_value(client, db_session):
    _admin(client, db_session)
    r = client.put("/api/settings/stock-track", json={"value": "maybe"})
    assert r.status_code == 422


def test_get_settings_masks_telegram_token_for_non_admin(client, db_session):
    _login(client, db_session, "seller1", "seller")
    set_setting(db_session, "telegram_bot_token", "1234567890:ABCDEF", user=None)
    r = client.get("/api/settings")
    assert r.status_code == 200
    token_row = next(s for s in r.json() if s["key"] == "telegram_bot_token")
    assert "1234567890:ABCDEF" not in token_row["value"]
    assert "****" in token_row["value"]
    assert "1234567890:ABCDEF" not in r.text
    # не-токенные настройки по-прежнему читаются
    assert any(s["key"] == "stock_track_default" and s["value"] == "true" for s in r.json())