import re

import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.dependencies import nav_is_current
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
        [href for _label, href in OLD_NAV_LINKS] + ["/orders/new"])


def test_the_brand_moved_from_the_sidebar_into_the_header(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    body = client.get("/").text
    header = body[body.index("<header"): body.index("</header>")]
    assert ("/", "СмартСклад") in _visible_links(header)


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
    assert "ui.sidebar_collapsed" not in keys
    assert not [key for key in keys if key.startswith("ui.")], (
        f"поользовательский ключ попал в общий список: {keys}"
    )
    expected = {
        "min_stock_days", "report_email_enabled", "report_email_period",
        "report_email_recipients", "report_email_time", "stock_track_default",
        "telegram_bot_token", "telegram_chat_id", "user_password_charset",
        "user_password_length",
    }
    assert set(keys) == expected, f"из списка настроек выпали или лишние глобалки: {sorted(keys)}"


def test_the_settings_page_does_not_list_the_per_user_menu_key(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    assert "sidebar_collapsed" not in client.get("/settings").text


def test_an_anonymous_page_renders_the_menu_without_a_sidebar_state(client):
    body = client.get("/login").text
    assert 'data-sidebar="expanded"' in body
    assert "Продажи" not in body


def _header(body: str) -> str:
    return body[body.index("<header"): body.index("</header>")]


def test_the_menu_button_sits_in_the_header_as_three_horizontal_bars(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    button = header[header.index("data-menu-toggle"):]
    button = button[: button.index("</button>")]
    assert "СмартСклад" in header, "бренд должен стоять рядом с кнопкой меню"
    path = button[button.index(' d="') + 4: button.index('"', button.index(' d="') + 4)]
    assert path == "M3 5h14M3 10h14M3 15h14", (
        f"кнопка обязана рисовать три горизонтальные полосы, а рисует {path!r}"
    )


def test_the_sidebar_footer_no_longer_carries_the_collapse_form(client, db_session):
    """Кнопка сворачивания переехала в шапку, в подвале сайдбара её больше нет.

    Раньше копий было две: «Свернуть меню» в подвале панели и «Показать меню» в
    шапке, и обе рендерились по разным условиям — состояние сайдбара читалось
    с двух сторон. Оставлена одна, в шапке.
    """
    _login_as(client, db_session, "boss", "admin")
    sidebar = _sidebar(client.get("/").text)
    assert 'action="/ui/sidebar"' not in sidebar, "в подвале сайдбара осталась форма сворачивания"
    assert "Свернуть меню" not in sidebar
    assert 'action="/ui/sidebar"' in _header(client.get("/").text)


@pytest.mark.parametrize("collapsed, sent", [(False, "true"), (True, "false")])
def test_the_collapse_control_in_the_header_posts_the_opposite_state(
        client, db_session, collapsed, sent):
    """Кнопка одна и шлёт противоположное текущему состояние.

    Раньше состояние читалось двумя разными формами, каждая со своим условием.
    Теперь форма одна и `collapsed` считается из текущего состояния, поэтому
    свёрнутое меню обязано возвращаться разворотом, а не второй копией кнопки.
    """
    _login_as(client, db_session, "boss", "admin")
    if collapsed:
        client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    header = _header(client.get("/").text)
    form = header[header.index('action="/ui/sidebar"'):]
    form = form[: form.index("</form>")]
    assert f'name="collapsed" value="{sent}"' in form, form


@pytest.mark.parametrize("collapsed, label", [
    (False, "Свернуть меню"),
    (True, "Развернуть меню"),
])
def test_the_collapse_button_keeps_its_aria_label(client, db_session, collapsed, label):
    """Значок без подписи читается вслух как «кнопка», и у кнопки это ничего не значит.

    У иконки `aria-hidden`, поэтому имя кнопки — единственное, что её называет.
    Оно же сообщает, что кнопка сделает, а не что нарисовано.
    """
    _login_as(client, db_session, "boss", "admin")
    if collapsed:
        client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    header = _header(client.get("/").text)
    form = header[header.index('action="/ui/sidebar"'):]
    form = form[: form.index("</form>")]
    found = re.findall(r'aria-label="([^"]*)"', form)
    assert found == [label], found


def test_the_header_block_holds_theme_then_collapse_then_add_order(client, db_session):
    """Порядок в блоке кнопок задан владельцем и проверяется на разметке.

    Порядок виден глазом сразу, поэтому ловится без браузера: тема, сворачивание,
    «Новый заказ». Формы и ссылка обязаны лежать внутри блока-поглотителя, иначе
    блок развалится на несколько групп.
    """
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    start = header.index("<header")
    spacer = header.index('class="ml-auto', start)
    theme = header.index('action="/ui/theme"', spacer)
    collapse = header.index('action="/ui/sidebar"', spacer)
    add = header.index('href="/orders/new"', spacer)
    assert spacer < theme < collapse < add, header[spacer:]
    block = header[spacer: header.index("</div>", add)]
    assert block.count("<form") == 2 and block.count("<a ") == 1, block


def test_the_add_order_control_in_the_header_opens_the_new_order_page(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    assert 'href="/orders/new"' in header
    assert 'aria-label="Новый заказ"' in header, (
        "ссылка без подписи называется только адресом, а значок скрыт от чтения"
    )
    assert client.get("/orders/new").status_code == 200


def test_the_add_order_control_is_absent_without_the_permission(client, db_session):
    """Складу заказ не создать, поэтому кнопки у него быть не должно.

    Кнопка шлёт на `/orders/new`, а страница без права `orders.create` отвечает
    403 — то есть безусловная кнопка была бы ссылкой в стену.
    """
    _login_as(client, db_session, "wh", "warehouseman")
    header = _header(client.get("/").text)
    assert 'action="/ui/theme"' in header, "тема остаётся доступной всем вошедшим"
    assert 'href="/orders/new"' not in header
    assert "Новый заказ" not in header
    assert client.get("/orders/new").status_code == 403


def test_every_icon_only_control_in_the_header_is_named(client, db_session):
    """Четыре значковые кнопки в ряд, и у каждой должно быть имя.

    Внутри кнопки только SVG с `aria-hidden`, то есть имя на самой кнопке —
    единственное, что её называет. Пустое имя хуже отсутствующего: значок у
    чтеца всё равно молчит, а имя на месте.
    """
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    buttons = re.findall(r"<button\b[^>]*>", header)
    assert len(buttons) == 3, buttons
    for tag in buttons:
        label = re.search(r'aria-label="([^"]*)"', tag)
        assert label and label.group(1).strip(), f"кнопка без имени: {tag}"
    assert header.count('role="group"') == 1
    assert 'aria-label="Панель действий"' in header


def test_the_hamburger_stays_at_the_far_left_and_stays_hidden_on_a_wide_screen(
        client, db_session):
    """Бургер — единственный способ открыть меню на телефоне, поэтому его трогать нельзя.

    На широком экране он уходит по `lg:hidden`, и тогда блок кнопок справа —
    единственный путь к сворачиванию. Неверная геометрия этого блока прятала
    единственный контроль; сам бургер при этом оставался исправным.
    """
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    hamburger = re.search(r"<button\b[^>]*data-menu-toggle[^>]*>", header)
    assert hamburger, header
    tag = hamburger.group(0)
    assert tag.count("lg:hidden") == 1, tag
    assert 'aria-label="Меню"' in tag
    assert 'aria-expanded="false"' in tag
    brand = header.index("СмартСклад")
    assert hamburger.start() < brand, "бургер обязан стоять левее бренда"
    inside = header[hamburger.start(): header.index("</button>", hamburger.start())]
    assert 'd="M3 5h14M3 10h14M3 15h14"' in inside, inside


def test_no_full_height_column_of_markup_sits_left_of_the_content(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    body = client.get("/").text
    assert "app-menu-toggle" not in body, (
        "div без правила растягивался на всю высоту и красил белую полосу слева"
    )
    head = body[body.index('class="flex min-h-screen flex-col"'): body.index("<aside")]
    head = head[head.index(">") + 1:]
    assert "<header" in head, "шапка обязана стоять первой в колонке"
    assert head.index("<header") < head.index("<div"), (
        f"шапку отодвинул посторонний блок: {head[:head.index('<header')]!r}"
    )
    layout = head[head.index("</header>") + len("</header>"):]
    assert layout.count("<div") == 1 and "<div" in layout, (
        f"слева от сайдбара лишние блоки растягиваются на всю высоту: {layout!r}"
    )


@pytest.mark.parametrize(
    "path, href, expected",
    [
        ("/", "/", True),
        ("/orders", "/orders", True),
        ("/orders/17", "/orders", True),
        ("/orders", "/orders-archive", False),
        ("/users-archive", "/users", False),
        ("/orders/17", "/", False),
        ("/login", "/orders", False),
    ],
)
def test_the_active_rule_matches_on_a_segment_boundary(path, href, expected):
    assert nav_is_current(path, href) is expected


def _current_links(markup: str) -> list[tuple[str, str]]:
    import re
    return [
        (href, label)
        for href, label in re.findall(
            r'href="([^"]+)"[^>]*aria-current="page"[^>]*>([^<]*)</a>', markup
        )
    ]


def test_the_menu_marks_the_section_of_a_nested_page(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    markup = _sidebar(client.get("/orders").text)
    assert _current_links(markup) == [("/orders", "Заказы")], markup
    assert 'class="app-nav-link app-nav-current"' in markup or (
        'font-semibold app-nav-current' in markup
    ), "отмеченный пункт обязан носить класс app-nav-current"


def test_the_menu_marks_nothing_on_a_path_that_matches_no_link(client):
    body = client.get("/reset-password").text
    assert _current_links(_sidebar(body)) == []
    assert _current_links(_header(body)) == []


@pytest.mark.parametrize(
    "path, href, label",
    [
        ("/products", "/products", "Товары"),
        ("/customers", "/customers", "Клиенты"),
        ("/units", "/units", "Единицы измерения"),
        ("/forecast", "/forecast", "Прогноз"),
        ("/import", "/import", "Импорт"),
        ("/roles", "/roles", "Роли"),
        ("/users", "/users", "Пользователи"),
        ("/settings", "/settings", "Настройки"),
        ("/notifications", "/notifications", "Уведомления"),
        ("/audit", "/audit", "Журнал аудита"),
        ("/backups", "/backups", "Резервные копии"),
        ("/dashboard", "/dashboard", "Аналитика"),
        ("/categories", "/categories", "Категории"),
        ("/reset-password", None, None),
        ("/users/{user_id}/edit", "/users", "Пользователи"),
    ],
)
def test_every_group_item_marks_its_own_section(client, db_session, path, href, label):
    """Пункты групп рендерит цикл, а не отдельная строка как «Заказы».

    Раньше отметка текущего раздела проверялась только на зашитой в шаблон ссылке
    «Заказы» (`base.html:65`), поэтому удаление `{% if %}` из цикла
    `base.html:73` оставляло весь прогон зелёным, а тринадцать разделов молча
    переставали подсвечиваться. Разбор: одно точное совпадение, одна вложенная
    страница и путь, не совпадающий ни с чем.
    """
    user = _login_as(client, db_session, "boss", "admin")
    markup = _sidebar(client.get(path.format(user_id=user.id)).text)
    expected = [] if href is None else [(href, label)]
    assert _current_links(markup) == expected, markup
    assert markup.count("app-nav-current") == len(expected), markup


def test_the_root_marks_the_brand_and_no_menu_item(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    body = client.get("/").text
    assert _current_links(_header(body)) == [("/", "СмартСклад")]
    assert _current_links(_sidebar(body)) == []