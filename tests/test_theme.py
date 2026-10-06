import re

import pytest
from starlette.requests import Request

from app.modules.audit.models import AuditLog
from app.modules.auth.dependencies import get_page_user
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Category, Product
from app.modules.inventory.service import get_unit_by_name
from app.modules.orders.service import create_order
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
        "report_email_recipients", "report_email_time", "smtp_from", "smtp_host",
        "smtp_password", "smtp_port", "smtp_user", "stock_track_default",
        "search_slow_ms", "telegram_bot_token", "telegram_chat_id",
        "user_password_charset",
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


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


def _mk_order(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=50)
    c = Customer(name="ООО Строй")
    db_session.add_all([p, c])
    db_session.commit()
    return create_order(db_session, customer_id=c.id, items=[{"product_id": p.id, "quantity": 2, "discount": 0}])


def _html_tag(body: str) -> str:
    start = body.index("<html")
    return body[start: body.index(">", start) + 1]


def _header(body: str) -> str:
    return body[body.index("<header"): body.index("</header>")]


def _switch_markup(body: str) -> str:
    header = _header(body)
    form = header[header.index('action="/ui/theme"'):]
    return form[: form.index("</form>")]


def test_the_page_carries_no_theme_class_by_default(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    assert "dark" not in _html_tag(client.get("/").text)


def test_the_dark_theme_puts_the_class_on_the_html_tag(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    assert 'class="dark"' in _html_tag(client.get("/").text)


def test_the_theme_survives_a_new_navigation(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    for path in ("/", "/products", "/settings", "/orders"):
        assert 'class="dark"' in _html_tag(client.get(path).text), path


def test_the_theme_belongs_to_the_user_and_not_to_the_browser(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    client.cookies.clear()
    _login_as(client, db_session, "second", "admin")
    assert "dark" not in _html_tag(client.get("/").text)


def test_the_switch_offers_the_other_theme_and_names_itself(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    body = client.get("/").text
    assert 'action="/ui/theme"' in body
    assert 'name="theme" value="dark"' in body
    assert "Тёмная тема" in body
    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    assert 'name="theme" value="light"' in client.get("/").text
    assert "Светлая тема" in client.get("/").text


def test_the_switch_sends_the_user_back_where_they_were(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    switch = _switch_markup(client.get("/products?page=2").text)
    assert 'name="next" value="/products?page=2"' in switch, (
        "форма переключателя темы обязана нести адрес текущей страницы: "
        f"{switch[:200]!r}"
    )


def _header_forms(header: str):
    forms = []
    for match in re.finditer(r"<form\b.*?</form>", header, re.DOTALL):
        tag = match.group(0)
        action = re.search(r'action="([^"]*)"', tag).group(1)
        classes = " ".join(re.findall(r'class="([^"]*)"', tag))
        forms.append((action, classes, match.start(), match.end()))
    return forms


@pytest.mark.parametrize("collapsed", [False, True])
def test_exactly_one_header_element_absorbs_the_free_space(client, db_session, collapsed):
    """`ml-auto` на двух детях flex-строки делит свободное место пополам.

    Свёрнутый сайдбар добавлял в шапку вторую форму, и без проверки кнопка темы
    оказывалась посреди шапки — ровно в том состоянии, которого почти не бывает.
    Теперь форм в шапке две (тема и сворачивание), стоят за ними кнопка-лупа и
    ссылка «Новый заказ», то есть повод вернуться ровно тот же. Проверка ловит
    сам класс дефекта: поглотитель свободного места в шапке ровно один, он
    открывается до всех форм и остаётся flex-строкой. Без последнего `ml-auto`
    в блоке, который не flex, работает ни на что, и блок кнопок уезжает в
    середину шапки — ровно тот дефект, который в прошлый раз прошёл весь прогон
    зелёным, потому что `test_tailwind_purge` проверяет, что класс есть в
    собранном CSS, а не что разметка им пользуется. Саму геометрию без браузера
    не проверить — она измерена отдельно, и на этой же шапке её теперь проверяет
    ещё и `tests/test_search_modal_geometry.py`: поиск открывается и не сдвигает
    ни высоту шапки, ни кнопки.
    """
    _login_as(client, db_session, "boss", "admin")
    if collapsed:
        client.post("/ui/sidebar", data={"collapsed": "true"}, follow_redirects=False)
    header = _header(client.get("/products").text)
    forms = _header_forms(header)
    assert [action for action, _c, _s, _e in forms] == ["/ui/theme", "/ui/sidebar"], forms
    assert header.index('action="/ui/sidebar"') < header.index("data-search-open"), (
        "лупа обязана стоять после сворачивания и до «Новый заказ» — это порядок владельца"
    )
    spacers = list(re.finditer(r'class="([^"]*\bml-auto\b[^"]*)"', header))
    assert len(spacers) == 1, (
        f"свободное место в шапке поглощают {len(spacers)} элементов, а обязан ровно один — "
        "иначе блок кнопок разъедется и уедет в середину шапки"
    )
    tokens = spacers[0].group(1).split()
    for required in ("flex", "items-center"):
        assert required in tokens, (
            f"поглотитель свободного места потерял класс {required!r}: {tokens} — "
            "без `flex` `ml-auto` не работает, и блок кнопок уезжает в середину шапки"
        )
    assert spacers[0].start() < forms[0][2], (
        "поглотитель свободного места обязан открываться до всех форм шапки, "
        "иначе блок кнопок окажется не прижат вправо"
    )
    assert header[spacers[0].start(): forms[-1][3]].count("</div>") == 0, (
        "обе формы обязаны лежать в том же блоке, что и поглотитель, "
        "иначе блок кнопок развалится на две группы"
    )


def test_the_switch_is_visible_on_a_phone(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    form = header[header.index('action="/ui/theme"'):]
    form = form[: form.index("</form>")]
    classes = re.findall(r'class="([^"]*)"', form)
    assert classes, "у формы переключателя нет ни одного класса"
    for name in classes:
        for token in name.split():
            assert not re.fullmatch(r"(?:[\w-]+:)?hidden", token), (
                f"переключатель темы спрятан на узком экране классом {token!r} — телефон его не увидит"
            )


def test_the_printed_order_stays_light_even_for_a_dark_user(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    order = _mk_order(db_session)
    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)
    body = client.get(f"/orders/{order.id}/print").text
    assert 'class="dark"' not in body, "тёмная тема протекла в печатный документ"


def test_the_anonymous_login_page_still_renders(client, db_session):
    assert client.get("/login").status_code == 200


PAGE_ROUTES = [
    "/", "/settings", "/notifications", "/roles", "/roles/new", "/users", "/users/new",
    "/products", "/products/new", "/dashboard", "/backups", "/import", "/forecast",
    "/audit", "/categories", "/categories/new", "/units", "/customers", "/customers/new",
    "/orders", "/orders/new", "/search", "/help",
    "/roles/{role_id}/edit", "/products/{product_id}/edit",
    "/categories/{category_id}/edit", "/customers/{customer_id}/edit",
]


def _with_ids(path: str, db_session) -> str:
    def _product():
        row = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0)
        db_session.add(row)
        db_session.commit()
        return row.id

    def _category():
        row = Category(name="Крепёж")
        db_session.add(row)
        db_session.commit()
        return row.id

    def _customer():
        row = Customer(name="ООО Строй")
        db_session.add(row)
        db_session.commit()
        return row.id

    makers = {
        "{role_id}": lambda: get_role_by_name(db_session, "admin").id,
        "{product_id}": _product,
        "{category_id}": _category,
        "{customer_id}": _customer,
    }
    for token, make in makers.items():
        if token in path:
            return path.replace(token, str(make()))
    return path


@pytest.mark.parametrize("path", PAGE_ROUTES)
def test_a_refused_theme_is_visible_on_every_page_the_switch_can_return_to(
        client, db_session, path):
    """Отказ темы возвращает на `next` — страница обязана его показать.

    Отказ не единственный носитель `?msg=`: в `main.py` 22 редиректа пишут его
    явно, а слот `{% if msg %}` есть на 24 страницах из 28. Но девять шаблонов
    рисуют слот как `bg-danger-bg`, и всякое сообщение на них — отказ: от
    переключателя либо от собственных POST-ов (`/orders/new` возвращает
    «Выберите клиента» и «Добавьте хотя бы одну позицию»). Зелёный на них был бы
    враньём, потому что успеха туда не приходит ниоткуда.

    Список зафиксирован целиком, включая четыре `{id}/edit`: у каждого своя
    страница и свой слот, и без записи они приехали бы без `msg` молча.
    """
    _login_as(client, db_session, "boss", "admin")
    path = _with_ids(path, db_session)
    r = client.post("/ui/theme", data={"theme": "sepia", "next": path},
                    follow_redirects=False)
    assert "msg=" in r.headers["location"], path
    body = client.get(r.headers["location"]).text
    assert "неизвестная тема" in body, f"{path} молча выкинула отказ"


SUN_GLYPH = 'd="M10 1v2M10 17v2'
MOON_GLYPH = 'd="M17 12.3A7.5'


def test_the_light_theme_shows_the_sun_and_the_dark_theme_the_moon(client, db_session):
    _login_as(client, db_session, "boss", "admin")

    light = _switch_markup(client.get("/").text)
    assert SUN_GLYPH in light, "в светлой теме значок не солнце"
    assert MOON_GLYPH not in light, "в светлой теме нарисован луна"

    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)

    dark = _switch_markup(client.get("/").text)
    assert MOON_GLYPH in dark, "в тёмной теме значок не луна"
    assert SUN_GLYPH not in dark, "в тёмной теме нарисован солнце"


def test_the_icon_agrees_with_the_label_and_the_submitted_theme(client, db_session):
    _login_as(client, db_session, "boss", "admin")

    light = _switch_markup(client.get("/").text)
    assert (SUN_GLYPH in light) == ('name="theme" value="dark"' in light), (
        "в светлой теме солнце должно означать переход в тёмную"
    )
    assert "Тёмная тема" in light

    client.post("/ui/theme", data={"theme": "dark"}, follow_redirects=False)

    dark = _switch_markup(client.get("/").text)
    assert (MOON_GLYPH in dark) == ('name="theme" value="light"' in dark), (
        "в тёмной теме луна должна означать переход в светлую"
    )
    assert "Светлая тема" in dark