"""Глобальный поиск из шапки: одно поле ищет по всем разделам сразу.

Владелец отказался от флажков по разделам, поэтому здесь ровно одно поле и три
раздела под ним. Проверяется то, что от этой простоты можно потерять:

* права. Человек не должен видеть строку того раздела, который он и так не
  открыл бы, — у склада нет `customers.manage`, и клиент в его результатах
  должен просто отсутствовать, а не быть «запрещённым»;
* пустой и короткий запрос. Пустой не сканирует таблицы, а короткий не
  приносит тысячу случайных совпадений;
* ограничение. Список на телефоне длиннее десяти строк в разделе нечитаем;
* совпадение с поиском на самой странице раздела: глобальный ищет теми же
  функциями списка, поэтому расхождение исключено по построению, и это тоже
  закреплено тестом.
"""

import re

from app.main import SEARCH_LIMIT, SEARCH_MIN_QUERY
from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name
from app.modules.orders.service import create_order
from tests.test_interactive_states import STATE_CLASSES

PASSWORD = "password123"


def _user(db_session, username, role):
    u = User(
        username=username,
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, role).id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _login_as(client, db_session, username, role):
    _user(db_session, username, role)
    session = login(db_session, username, PASSWORD)
    client.cookies.set("session", session.token)


def _login_as_role(client, db_session, username, permissions):
    role = Role(name=username + "-role", permissions=list(permissions), is_super=False)
    db_session.add(role)
    db_session.commit()
    u = User(username=username, password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    session = login(db_session, username, PASSWORD)
    client.cookies.set("session", session.token)


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


def _seed(db_session):
    product = Product(name="Гвозди 100", sku="ART-777", unit_id=_unit_id(db_session),
                      price=10.0, rest=50)
    customer = Customer(name="ООО Ромашка 100", phone="+7 900 123-45-67", inn="7701234567")
    db_session.add_all([product, customer])
    db_session.commit()
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": product.id, "quantity": 2, "discount": 0}])
    return product, customer, order


def _header(body: str) -> str:
    return body[body.index("<header"): body.index("</header>")]


def test_a_customer_is_found_by_name(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _product, customer, _order = _seed(db_session)
    body = client.get("/search", params={"q": "Ромашка"}).text
    assert "ООО Ромашка" in body
    assert f'href="/customers/{customer.id}"' in body, "клиент должен вести на свою карточку"
    assert "Клиенты" in body


def test_a_customer_is_found_by_phone(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    assert "ООО Ромашка" in client.get("/search", params={"q": "900 123"}).text


def test_a_customer_is_found_by_inn(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    body = client.get("/search", params={"q": "770123"}).text
    assert "ООО Ромашка" in body
    assert "7701234567" in body, "ИНН должен быть виден в строке результата"


def test_an_order_is_found_by_number(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _product, _customer, order = _seed(db_session)
    body = client.get("/search", params={"q": order.number}).text
    assert f'href="/orders/{order.id}"' in body
    assert order.number in body


def test_a_product_is_found_by_name(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    product, _customer, _order = _seed(db_session)
    body = client.get("/search", params={"q": "Гвозди"}).text
    assert f'href="/products/{product.id}"' in body
    assert "Гвозди 100" in body


def test_a_product_is_found_by_article(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    body = client.get("/search", params={"q": "ART-777"}).text
    assert "Гвозди 100" in body
    assert "ART-777" in body, "артикул должен быть виден в строке результата"


def test_the_result_agrees_with_the_search_of_the_section_page(client, db_session):
    """Глобальный поиск зовёт те же list_*, что и страницы разделов.

    Расхождение между ними — дефект, который не заметил бы никто: человек ищет
    в шапке, не находит и решает, что товара нет. Поэтому сверяется не текст,
    а сами строки: тот же запрос на странице раздела даёт тот же id.
    """
    _login_as(client, db_session, "boss", "admin")
    product, customer, order = _seed(db_session)
    body = client.get("/search", params={"q": "Гвозди"}).text
    assert f'href="/products/{product.id}"' in body
    on_page = client.get("/products", params={"search": "Гвозди"}).text
    assert f'href="/products/{product.id}"' in on_page
    assert "ООО Ромашка" not in body, "поиск по товару не должен выдавать клиентов"
    assert f'href="/customers/{customer.id}"' in client.get(
        "/customers", params={"search": "Ромашка"}).text
    assert f'href="/orders/{order.id}"' in client.get(
        "/orders", params={"search": order.number}).text


def test_the_magnifier_lives_in_the_header_block_between_collapse_and_add_order(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    collapse = header.index('action="/ui/sidebar"')
    search = header.index('action="/search"')
    add = header.index('href="/orders/new"')
    assert collapse < search < add, header[collapse:add]
    assert '<details class="app-search' in header
    spacer = header.index('class="ml-auto')
    assert spacer < search < header.index("</div>", add), "поиск обязан лежать в блоке кнопок"


def test_the_magnifier_is_hidden_from_an_anonymous_visitor(client):
    body = client.get("/login").text
    assert 'action="/search"' not in body
    assert "app-search" not in body


def test_the_search_answers_an_anonymous_visitor_with_the_login_page(client, db_session):
    _seed(db_session)
    r = client.get("/search", params={"q": "Гвозди"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_the_magnifier_is_open_on_the_results_page_and_keeps_the_query(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    header = _header(client.get("/search", params={"q": "Гвозди"}).text)
    assert "<details" in header and " open>" in header, "поле должно быть раскрыто на странице ответа"
    assert 'name="q" value="Гвозди"' in header, "поле обязано сохранить введённое"


def test_the_input_is_a_details_element_and_needs_no_script(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    assert "<summary" in header and "</summary>" in header
    assert header.count("<script") == 0, "раскрытие поиска сделано скриптом, а оно не нужно"


def test_the_magnifier_carries_exactly_one_state_class(client, db_session):
    """`<summary>` не входит в TAG сторожа интерактивных состояний.

    Ссылка и кнопка там проверяются, а раскрывающий элемент — нет, поэтому его
    состояние проверяется здесь: без `app-btn` у лупы не будет ни наведения, ни
    рамки, и она выглядела бы как чужеродная точка в ряду кнопок.
    """
    _login_as(client, db_session, "boss", "admin")
    header = _header(client.get("/").text)
    tag = re.search(r"<summary\b[^>]*>", header).group(0)
    classes = set(re.search(r'class="([^"]*)"', tag).group(1).split())
    assert len(classes & STATE_CLASSES) == 1, tag


def test_the_disclosure_marker_is_removed_from_the_built_stylesheet(client, db_session):
    """Штатный треугольник сдвинул бы значок лупы внутри квадрата 44 на 44."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text("utf-8")
    assert re.search(r"\.app-search summary\{[^}]*list-style:none", css), "маркер не убран"
    assert re.search(r"\.app-search summary::?-webkit-details-marker\{display:none\}", css), (
        "маркер убран только для одного движка, в вебкит-браузере он останется"
    )


def test_the_focus_ring_covers_the_magnifier(client, db_session):
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text("utf-8")
    assert re.search(r":where\(a,button,input,select,textarea,summary\):focus-visible\{", css), (
        "раскрывающая кнопка поиска не входит в кольцо фокуса и осталась с "
        "обводкой браузера"
    )


def test_a_warehouseman_gets_no_customer_hit(client, db_session):
    """У склада нет `customers.manage` — клиент в результатах отсутствует."""
    _login_as(client, db_session, "wh", "warehouseman")
    _seed(db_session)
    body = client.get("/search", params={"q": "Ромашка"}).text
    assert "ООО Ромашка" not in body
    assert f'href="/customers/' not in body
    assert "Клиенты" not in body, "запрещённый раздел не должен даже называться"


def test_a_warehouseman_still_gets_product_and_order_hits(client, db_session):
    """Проверка выше не проходит, если search отдаёт пустое всегда."""
    _login_as(client, db_session, "wh", "warehouseman")
    product, _customer, order = _seed(db_session)
    body = client.get("/search", params={"q": "Гвозди"}).text
    assert f'href="/products/{product.id}"' in body
    body = client.get("/search", params={"q": order.number}).text
    assert f'href="/orders/{order.id}"' in body


def test_a_user_without_products_view_gets_no_product_hit(client, db_session):
    _login_as_role(client, db_session, "noprod", ["orders.view", "customers.manage"])
    product, _customer, _order = _seed(db_session)
    body = client.get("/search", params={"q": "Гвозди"}).text
    assert f'href="/products/{product.id}"' not in body
    assert "Гвозди 100" not in body
    assert "Товары" not in body


def test_a_user_without_orders_view_gets_no_order_hit(client, db_session):
    _login_as_role(client, db_session, "noord", ["products.view", "customers.manage"])
    _product, _customer, order = _seed(db_session)
    body = client.get("/search", params={"q": order.number}).text
    assert f'href="/orders/{order.id}"' not in body
    assert "Заказы" not in body


def test_an_empty_query_says_what_to_type_and_scans_nothing(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    body = client.get("/search").text
    assert "Введите запрос" in body
    assert "ООО Ромашка" not in body
    assert "Гвозди 100" not in body
    assert "Ничего не найдено" not in body, "пустой запрос не должен выглядеть как промах"


def test_a_whitespace_query_is_an_empty_query(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    body = client.get("/search", params={"q": "   "}).text
    assert "Введите запрос" in body
    assert "Гвозди 100" not in body


def test_a_query_shorter_than_the_minimum_is_refused_before_the_search(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    body = client.get("/search", params={"q": "Г"}).text
    assert f"хотя бы {SEARCH_MIN_QUERY} символа" in body
    assert "Гвозди 100" not in body


def test_the_minimum_is_two_characters(client):
    assert SEARCH_MIN_QUERY == 2, "минимум поменялся — перечитай отчёт и документацию"


def test_a_query_that_matches_nothing_says_so_and_keeps_the_query(client, db_session):
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    body = client.get("/search", params={"q": "ZZZ-нет-такого"}).text
    assert "Ничего не найдено" in body
    assert 'name="q" value="ZZZ-нет-такого"' in body
    assert "найдено: 0" in body


def test_the_results_are_capped_per_section_and_the_cap_is_visible(client, db_session):
    """Числа в тесте написаны руками, а не взяты из SEARCH_LIMIT.

    С expectation из самой константы проверка не могла бы упасть: подняли
    ограничение до 100 — тест ожидал 100 и радовался. Двадцать пять товаров
    против десяти показанных — это то, что человек видит.
    """
    _login_as(client, db_session, "boss", "admin")
    unit = _unit_id(db_session)
    for n in range(25):
        db_session.add(Product(name=f"Гвозди {n:02d}", sku=f"ART-{n:02d}",
                               unit_id=unit, price=1.0, rest=1))
    db_session.commit()
    body = client.get("/search", params={"q": "Гвозди"}).text
    assert body.count('href="/products/') == 10
    assert "показано 10 из 25" in body, "обрезанный список обязан говорить, что он обрезан"


def test_the_limit_is_ten_rows(client):
    assert SEARCH_LIMIT == 10


def _titles(body):
    return re.findall(r'<h2 class="app-group-title">([^<]+):', body)


def test_hits_are_grouped_by_section_in_the_sidebar_order(client, db_session):
    """Разделы идут в том же порядке, что в меню: клиент, товар, заказ.

    Сортировка по имени вперемешку с разделами читалась бы как каша, поэтому
    порядок задан один на всю страницу, а внутри раздела — тем порядком, который
    уже задала сама страница раздела.
    """
    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    # «100» есть и в названии клиента, и в названии товара, и — с 04.10 — в имени
    # клиента заказа: поиск заказов смотрит и в имя клиента, а не только в номер.
    both = _titles(client.get("/search", params={"q": "100"}).text)
    assert both == ["Клиенты", "Товары", "Заказы"], both
    one = _titles(client.get("/search", params={"q": "Гвозди"}).text)
    assert one == ["Товары"], one
    number = _titles(client.get("/search", params={"q": "00001"}).text)
    assert number == ["Заказы"], number


def test_every_hit_carries_its_own_label_and_a_real_link(client, db_session):
    """Ссылка ведёт на саму сущность, а не на адрес поиска с идентификатором."""
    _login_as(client, db_session, "boss", "admin")
    product, customer, order = _seed(db_session)
    body = client.get("/search", params={"q": "Гвозди"}).text
    hrefs = re.findall(r'<a href="([^"]+)" class="app-link text-accent">', body)
    assert hrefs, "в ответе нет ни одной ссылки"
    for href in hrefs:
        assert not href.startswith("/search"), href
        assert re.fullmatch(r"/(customers|products|orders)/\d+", href), href
    assert f'href="/products/{product.id}"' in body
    assert f'href="/customers/{customer.id}"' in client.get(
        "/search", params={"q": "Ромашка"}).text
    assert f'href="/orders/{order.id}"' in client.get(
        "/search", params={"q": order.number}).text


def test_a_search_is_not_a_mutation(client, db_session):
    from app.modules.audit.models import AuditLog

    _login_as(client, db_session, "boss", "admin")
    _seed(db_session)
    before = db_session.query(AuditLog).count()
    client.get("/search", params={"q": "Гвозди"})
    client.get("/search", params={"q": "Ромашка"})
    assert db_session.query(AuditLog).count() == before, "поиск не должен писать в аудит"
