import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password
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


def _user(db_session, username="boss"):
    u = User(
        username=username,
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, "admin").id,
    )
    db_session.add(u)
    db_session.commit()
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