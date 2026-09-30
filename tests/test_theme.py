import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.settings.service import (
    DEFAULT_THEME,
    THEME_KEY,
    THEME_VALUES,
    is_user_setting,
    set_setting,
    set_user_theme,
    user_setting_key,
    user_theme,
)

PASSWORD = "password123"


def _user(db_session, username="boss", role="admin"):
    u = User(
        username=username,
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, role).id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _login_as(client, db_session, username, role):
    u = _user(db_session, username, role)
    session = login(db_session, username, PASSWORD)
    client.cookies.set("session", session.token)
    return u


def test_a_user_without_the_key_gets_the_light_theme(db_session):
    u = _user(db_session)
    assert user_theme(db_session, u.id) == DEFAULT_THEME
    assert DEFAULT_THEME == "light"


def test_the_choice_is_stored_per_user(db_session):
    u = _user(db_session, "boss")
    other = _user(db_session, "second")
    set_user_theme(db_session, u.id, "dark")
    assert user_theme(db_session, u.id) == "dark"
    assert user_theme(db_session, other.id) == "light"


def test_both_values_survive_a_round_trip(db_session):
    u = _user(db_session)
    for value in THEME_VALUES:
        set_user_theme(db_session, u.id, value)
        assert user_theme(db_session, u.id) == value
    assert set(THEME_VALUES) == {"light", "dark"}


def test_writing_the_theme_leaves_an_audit_trail(db_session):
    u = _user(db_session)
    set_user_theme(db_session, u.id, "dark", user=u)
    rows = db_session.query(AuditLog).filter(
        AuditLog.action == "settings.update",
        AuditLog.entity_type == "settings",
    ).all()
    assert [r.new_value for r in rows] == ["dark"]


def test_an_unknown_value_is_refused_rather_than_stored(db_session):
    u = _user(db_session)
    with pytest.raises(ValueError):
        set_user_theme(db_session, u.id, "sepia")
    assert user_theme(db_session, u.id) == DEFAULT_THEME


def test_a_garbage_already_in_the_database_renders_as_light(db_session):
    u = _user(db_session)
    set_setting(db_session, user_setting_key(THEME_KEY, u.id), "chartreuse")
    assert user_theme(db_session, u.id) == DEFAULT_THEME


def test_the_theme_key_is_treated_as_a_per_user_key():
    assert is_user_setting("ui.theme:7") is True
    assert is_user_setting("ui.theme") is False
    assert is_user_setting("ui.appearance") is False


def test_the_settings_api_hides_the_written_theme_choice(client, db_session):
    u = _login_as(client, db_session, "boss", "admin")
    set_user_theme(db_session, u.id, "dark", user=u)
    keys = [row["key"] for row in client.get("/api/settings").json()]
    assert not [key for key in keys if key.startswith("ui.")], (
        f"пользовательский ключ попал в общий список: {keys}"
    )
    assert "ui.theme" not in keys
    expected = {
        "min_stock_days", "report_email_enabled", "report_email_period",
        "report_email_recipients", "report_email_time", "stock_track_default",
        "telegram_bot_token", "telegram_chat_id", "user_password_charset",
        "user_password_length",
    }
    assert set(keys) == expected, f"из списка настроек выпали или лишние глобалки: {sorted(keys)}"