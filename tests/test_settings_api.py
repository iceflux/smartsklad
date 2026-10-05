import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.settings.service import (
    SEARCH_SLOW_MS_KEY,
    SEARCH_SLOW_MS_MAX,
    SEARCH_SLOW_MS_MIN,
    get_setting,
    set_setting,
)

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


def test_put_search_slow_requires_the_settings_right(client, db_session):
    _login(client, db_session, "seller1", "seller")  # seller не имеет settings.change
    r = client.put("/api/settings/search-slow", json={"value": 1500})
    assert r.status_code == 403
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1000", (
        "отказ по правам записал значение всё равно"
    )


def test_put_search_slow_saves_and_reads_back(client, db_session):
    _admin(client, db_session)
    r = client.put("/api/settings/search-slow", json={"value": 1500})
    assert r.status_code == 200
    assert r.json() == {"key": SEARCH_SLOW_MS_KEY, "value": "1500"}
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1500"
    assert any(s["key"] == SEARCH_SLOW_MS_KEY and s["value"] == "1500"
               for s in client.get("/api/settings").json())


def test_put_search_slow_audits_the_change(client, db_session):
    _admin(client, db_session)
    client.put("/api/settings/search-slow", json={"value": 1500})
    row = (db_session.query(AuditLog)
           .filter(AuditLog.action == "settings.update", AuditLog.entity_type == "settings")
           .order_by(AuditLog.id.desc()).first())
    assert row is not None
    assert row.new_value == "1500"
    assert row.old_value == "1000"


@pytest.mark.parametrize("value", [0, 199, 5001, 60000, -1])
def test_put_search_slow_refuses_values_outside_the_range(client, db_session, value):
    """Отказ, а не усечение: молча подогнанное значение выглядело бы как
    сохранённое и вело бы себя не так, как показано в поле. 60000 — тот
    случай, о котором предупреждает задание: порог, при котором автопоиск
    не отключается никогда."""
    _admin(client, db_session)
    r = client.put("/api/settings/search-slow", json={"value": value})
    assert r.status_code == 422, r.text
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1000"


def test_put_search_slow_accepts_the_range_boundaries(client, db_session):
    _admin(client, db_session)
    for value in (SEARCH_SLOW_MS_MIN, SEARCH_SLOW_MS_MAX):
        r = client.put("/api/settings/search-slow", json={"value": value})
        assert r.status_code == 200, r.text
        assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == str(value)


@pytest.mark.parametrize("value", ["быстро", None, "1000.5", [1000]])
def test_put_search_slow_refuses_a_value_that_is_not_a_whole_number(client, db_session, value):
    _admin(client, db_session)
    r = client.put("/api/settings/search-slow", json={"value": value})
    assert r.status_code == 422, r.text
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1000"