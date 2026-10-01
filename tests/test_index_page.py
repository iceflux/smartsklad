from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"
ROLES = ["seller", "warehouseman", "manager", "admin"]


def _login_as(client, db_session, username, role):
    u = User(
        username=username,
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, role).id,
    )
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)
    return u


def test_index_redirects_anonymous_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_index_does_not_loop_for_an_anonymous_visitor(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.url.path == "/login"
    assert "Вход" in r.text


def test_index_redirects_to_the_orders_list(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/orders"


def test_index_keeps_a_message_coming_from_the_theme_switch(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    posted = client.post("/ui/theme", data={"theme": "sepia", "next": "/"},
                         follow_redirects=False)
    assert "msg=" in posted.headers["location"]
    r = client.get(posted.headers["location"], follow_redirects=False)
    assert r.headers["location"].startswith("/orders?"), r.headers["location"]
    assert "неизвестная тема" in client.get(posted.headers["location"]).text


def test_index_is_not_a_second_page_with_its_own_menu(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    body = client.get("/").text
    assert "Добро пожаловать" not in body, "приветствие главной страницы осталось"
    for section in ("/forecast", "/dashboard", "/import", "/backups", "/notifications"):
        assert f'href="{section}"' in body, f"в меню нет раздела {section}"


def test_the_index_template_is_gone_from_the_project():
    from pathlib import Path

    template = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"
    assert not template.exists(), (
        "index.html вернулся: это вторая копия меню, у которой нет ни одного "
        "раздела из четырнадцати"
    )


def test_every_default_role_reaches_the_orders_list_from_the_root(client, db_session):
    for index, role in enumerate(ROLES):
        _login_as(client, db_session, f"user{index}", role)
        r = client.get("/")
        assert r.status_code == 200, role
        assert r.url.path == "/orders", role
