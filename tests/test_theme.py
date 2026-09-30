import pytest
from starlette.requests import Request

from app.modules.audit.models import AuditLog
from app.modules.auth.dependencies import get_page_user
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


def _page_request(token=None) -> Request:
    headers = [(b"cookie", f"session={token}".encode())] if token else []
    return Request({"type": "http", "headers": headers})


def test_switching_the_theme_redirects_back_to_the_page(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    r = client.post("/ui/theme", data={"theme": "dark", "next": "/products"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/products"


def test_switching_the_theme_writes_the_user_setting(client, db_session):
    u = _login_as(client, db_session, "boss", "admin")
    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    assert user_theme(db_session, u.id) == "dark"


def test_an_unknown_theme_is_refused_and_stores_nothing(client, db_session):
    u = _login_as(client, db_session, "boss", "admin")
    r = client.post("/ui/theme", data={"theme": "sepia", "next": "/products"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert user_theme(db_session, u.id) == DEFAULT_THEME


def test_every_accepted_theme_lands(client, db_session):
    u = _login_as(client, db_session, "boss", "admin")
    for value in THEME_VALUES:
        r = client.post("/ui/theme", data={"theme": value}, follow_redirects=False)
        assert "msg=" not in r.headers["location"], value
        assert user_theme(db_session, u.id) == value, value


def test_a_refused_theme_still_says_why(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    r = client.post("/ui/theme", data={"theme": "sepia", "next": "/products"},
                    follow_redirects=False)
    assert "msg=" in r.headers["location"]


def test_a_refused_theme_keeps_the_query_string_of_the_page(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    r = client.post("/ui/theme", data={"theme": "", "next": "/products?page=2"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/products?page=2&msg=")


def test_the_theme_switch_needs_a_session(client):
    r = client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_the_theme_switch_refuses_to_redirect_off_site(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    for hostile in ("//evil.example", "https://evil.example"):
        r = client.post("/ui/theme", data={"theme": "dark", "next": hostile},
                        follow_redirects=False)
        assert r.headers["location"] == "/", hostile


def test_switching_the_theme_leaves_an_audit_trail(client, db_session):
    u = _login_as(client, db_session, "boss", "admin")
    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    rows = db_session.query(AuditLog).filter(
        AuditLog.action == "settings.update", AuditLog.new_value == "dark").all()
    assert len(rows) == 1
    assert rows[0].user_id == u.id


def test_the_page_request_carries_the_theme(db_session):
    anonymous = _page_request()
    assert get_page_user(anonymous, db_session) is None
    assert anonymous.state.theme == DEFAULT_THEME

    u = _user(db_session)
    token = login(db_session, "boss", PASSWORD).token
    request = _page_request(token)
    assert get_page_user(request, db_session).id == u.id
    assert request.state.theme == DEFAULT_THEME

    set_user_theme(db_session, u.id, "dark")
    request = _page_request(token)
    get_page_user(request, db_session)
    assert request.state.theme == "dark"