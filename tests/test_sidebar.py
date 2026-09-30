import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.settings.models import Setting

PASSWORD = "password123"

OLD_NAV_LINKS = [
    ("Товары", "/products"),
    ("Категории", "/categories"),
    ("Клиенты", "/customers"),
    ("Заказы", "/orders"),
    ("Прогноз", "/forecast"),
    ("Аналитика", "/dashboard"),
    ("Резервные копии", "/backups"),
    ("Импорт", "/import"),
    ("Роли", "/roles"),
    ("Пользователи", "/users"),
    ("Единицы измерения", "/units"),
    ("Журнал аудита", "/audit"),
    ("Уведомления", "/notifications"),
    ("Настройки", "/settings"),
]

SIDEBAR_ORDER = [
    ("/", "СмартСклад"),
    ("/orders/new", "Новый заказ"),
    ("/orders", "Заказы"),
    ("/customers", "Клиенты"),
    ("/products", "Товары"),
    ("/categories", "Категории"),
    ("/units", "Единицы измерения"),
    ("/dashboard", "Аналитика"),
    ("/forecast", "Прогноз"),
    ("/import", "Импорт"),
    ("/backups", "Резервные копии"),
    ("/audit", "Журнал аудита"),
    ("/roles", "Роли"),
    ("/users", "Пользователи"),
    ("/settings", "Настройки"),
    ("/notifications", "Уведомления"),
]


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


def _sidebar(body: str) -> str:
    start = body.index('<aside class="app-sidebar')
    return body[start:body.index("</aside>")]


def _visible_links(markup: str) -> list[tuple[str, str]]:
    import re
    return [
        (href, re.sub(r"<[^>]+>", "", label).strip())
        for href, label in re.findall(r'href="([^"]+)"[^>]*>([^<]*)</a>', markup)
    ]


@pytest.mark.parametrize("href", [href for _label, href in OLD_NAV_LINKS])
def test_no_link_of_the_old_flat_nav_disappeared(client, db_session, href):
    _login_as(client, db_session, "boss", "admin")
    body = client.get("/").text
    assert href in _sidebar(body), f"{href} был в плоской шапке и пропал из сайдбара"


def test_the_sidebar_lists_exactly_the_old_links_plus_the_new_order_button(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    found = _visible_links(_sidebar(client.get("/").text))
    assert found == SIDEBAR_ORDER
    assert sorted(href for href, _label in found) == sorted(
        [href for _label, href in OLD_NAV_LINKS] + ["/", "/orders/new"])


def test_the_sidebar_still_carries_the_logout_and_the_login_link(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    assert "action=\"/logout\"" in _sidebar(client.get("/").text)
    _login_as(client, db_session, "nobody", "admin")
    client.cookies.clear()
    assert "/login" in _sidebar(client.get("/login").text)


def test_a_group_without_visible_items_omits_its_heading(client, db_session):
    _login_as(client, db_session, "wh", "warehouseman")
    markup = _sidebar(client.get("/").text)
    assert "Склад" in markup and "/products" in markup
    for title in ("Продажи", "Анализ", "Система"):
        assert title not in markup, f"у роли нет прав ни на один пункт группы «{title}»"


def test_every_group_with_a_visible_item_keeps_its_heading(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    markup = _sidebar(client.get("/").text)
    for title in ("Продажи", "Склад", "Анализ", "Система"):
        assert title in markup


def test_the_menu_starts_expanded_for_a_user_without_the_setting(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    assert db_session.query(Setting).filter(Setting.key.like("ui.%")).count() == 0
    assert 'data-sidebar="expanded"' in client.get("/").text


def test_collapsing_the_menu_writes_the_user_setting_and_is_rendered_back(client, db_session):
    user = _login_as(client, db_session, "boss", "admin")
    r = client.post("/ui/sidebar", data={"collapsed": "true", "next": "/products"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/products"
    row = db_session.query(Setting).filter(
        Setting.key == f"ui.sidebar_collapsed:{user.id}").one()
    assert row.value == "true"
    body = client.get("/").text
    assert 'data-sidebar="collapsed"' in body
    assert 'value="false"' in body, "свёрнутое состояние обязано быть чем-то возвращено"


def test_uncollapsing_the_menu_rewrites_the_setting(client, db_session):
    user = _login_as(client, db_session, "boss", "admin")
    client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    client.post("/ui/sidebar", data={"collapsed": "false"}, follow_redirects=False)
    assert db_session.query(Setting).filter(
        Setting.key == f"ui.sidebar_collapsed:{user.id}").one().value == "false"
    assert 'data-sidebar="expanded"' in client.get("/").text


def test_the_menu_keeps_the_query_string_of_the_page_it_was_toggled_on(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    r = client.post("/ui/sidebar", data={"collapsed": "true", "next": "/products?page=2"},
                    follow_redirects=False)
    assert r.headers["location"] == "/products?page=2"


def test_the_collapsed_state_belongs_to_the_user_and_not_to_the_browser(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    assert 'data-sidebar="collapsed"' in client.get("/").text
    _login_as(client, db_session, "second", "admin")
    assert 'data-sidebar="expanded"' in client.get("/").text


def test_toggling_the_menu_needs_a_session(client):
    r = client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_the_toggle_refuses_to_redirect_off_site(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    for hostile in ("https://example.com", "//example.com", "http://example.com", "products"):
        r = client.post("/ui/sidebar", data={"collapsed": "true", "next": hostile},
                        follow_redirects=False)
        assert r.headers["location"] == "/", hostile


def test_the_toggle_leaves_an_audit_trail(client, db_session):
    user = _login_as(client, db_session, "boss", "admin")
    client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    rows = db_session.query(AuditLog).filter(
        AuditLog.action == "settings.update", AuditLog.entity_type == "settings").all()
    assert len(rows) == 1
    assert rows[0].user_id == user.id
    assert rows[0].new_value == "true"
    assert rows[0].old_value is None


def test_the_settings_api_hides_the_per_user_menu_key(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    keys = [row["key"] for row in client.get("/api/settings").json()]
    assert keys, "список настроек опустел целиком — фильтр съел лишнее"
    assert not [key for key in keys if key.startswith("ui.")]


def test_the_settings_page_does_not_list_the_per_user_menu_key(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    assert "sidebar_collapsed" not in client.get("/settings").text


def test_an_anonymous_page_renders_the_menu_without_a_sidebar_state(client):
    body = client.get("/login").text
    assert 'data-sidebar="expanded"' in body
    assert "Продажи" not in body