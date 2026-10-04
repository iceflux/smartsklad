"""Подстановка клиента и товара в заказе: поиск по мере набора вместо списка.

Список из первых 25 строк перестал быть ограничением: в базе владельца 995
клиентов и 5854 товара, и открывать такой список в секунду. Механизм один на
обоих — текстовое поле с отфильтрованным списком под ним, а поиск идёт ТЕМИ ЖЕ
`list_customers`/`list_products`, что и страницы разделов, и шапка: второй
путь запросов означал бы, что глобальный поиск и форма заказа разойдутся, и
никто этого не заметит.

Проверяется здесь серверная половина (что отдаёт эндпоинт и что рисует форма) и
то, что выбор не теряется. Клиентская половина — пауза, стрелки, Enter и
Escape — проверена в `tests/test_order_picker_script_js.py`.
"""

from html.parser import HTMLParser

from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name

PASSWORD = "password123"


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _seed(db_session):
    product = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session),
                      price=100.0, rest=10)
    customer = Customer(name="ООО Строй", phone="+7-900-000-00-00", inn="7707080000")
    db_session.add_all([product, customer])
    db_session.commit()
    return product, customer


def _pad_products(client, count):
    for i in range(count):
        r = client.post("/api/products", json={
            "name": f"{i:03d} заготовка", "sku": f"FILLER-{i:03d}",
            "price": 10.0, "rest": 100})
        assert r.status_code == 201, r.text


class _Pickers(HTMLParser):
    """Разбирает блоки подстановки: скрытое поле несёт идентификатор,
    видимое — имя, а само поле не имеет `name` и потому в форму не уходит."""

    def __init__(self):
        super().__init__()
        self.current = None
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "app-picker" in attrs.get("class", ""):
            self.current = {"attrs": attrs, "hidden": {}, "text": None}
            self.blocks.append(self.current)
        elif self.current is not None and tag == "input":
            if attrs.get("type") == "hidden":
                self.current["hidden"] = attrs
            elif attrs.get("type") == "text":
                self.current["text"] = attrs

    def handle_endtag(self, tag):
        if tag == "div" and self.current is not None:
            self.current = None


def _pickers(body):
    parser = _Pickers()
    parser.feed(body)
    return parser.blocks


def _of(blocks, kind, value):
    found = [b for b in blocks
             if b["hidden"].get("value") == str(value)
             and b["attrs"].get("data-lookup") == f"/api/lookup/{kind}"]
    assert found, f"в форме нет подстановки {kind} со значением {value}"
    return found[0]


def test_the_lookup_answers_with_the_same_rows_the_search_page_shows(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    db_session.add(Customer(name="Ромашка"))
    db_session.commit()
    r = client.get("/api/lookup/customers", params={"q": "Ромаш"})
    assert r.status_code == 200
    assert [row["label"] for row in r.json()["items"]] == ["Ромашка"]


def test_the_lookup_searches_the_same_columns_the_section_page_does(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    db_session.add_all([
        Customer(name="Телефонный", phone="+7-900-111-22-33"),
        Customer(name="Иннный", inn="7707080001"),
    ])
    db_session.commit()
    assert [row["label"] for row in
            client.get("/api/lookup/customers", params={"q": "+7-900-111"}).json()["items"]
            ] == ["Телефонный"]
    assert [row["label"] for row in
            client.get("/api/lookup/customers", params={"q": "7707080001"}).json()["items"]
            ] == ["Иннный"]
    product = Product(name="Доска", sku="BOARD-77", unit_id=_unit_id(db_session),
                      price=1.0, rest=1)
    db_session.add(product)
    db_session.commit()
    assert [row["label"] for row in
            client.get("/api/lookup/products", params={"q": "BOARD-77"}).json()["items"]
            ] == ["Доска"]


def test_the_lookup_returns_at_most_the_cap_and_says_how_many_it_left_out(client, db_session):
    _admin(client, db_session)
    for i in range(14):
        db_session.add(Customer(name=f"Гость {i:03d}"))
    db_session.commit()
    body = client.get("/api/lookup/customers", params={"q": "Гость"}).json()
    assert len(body["items"]) == 10
    assert body["total"] == 14
    assert body["limit"] == 10

def test_a_single_letter_is_already_a_search(client, db_session):
    """Порог подстановки — один символ, и это решение, а не опечатка. Подстановка
    отвечает на вопрос «что положить в строку заказа», её выдача ограничена
    десятью строками с видимой обрезкой, и глобальный поиск сразу по трём
    разделам тут ни при чём. Причина и измерения — в `_lookup_query`."""
    _admin(client, db_session)
    _seed(db_session)
    body = client.get("/api/lookup/customers", params={"q": "О"}).json()
    assert [row["label"] for row in body["items"]] == ["ООО Строй"], body
    assert "recent" not in body, "одна буква отдала список последних выбранных"


def test_the_two_minimums_are_pinned_and_differ_on_purpose(client, db_session):
    """Пороги разные намеренно, и оба числа зафиксированы: молча сровнять их —
    значит либо вернуть владельцу отказ на первой букве, либо заставить поиск из
    шапки показывать сотни строк. Клиентский скрипт обязан держать тот же порог,
    что и сервер, иначе первая буква уйдёт в базу и вернёт пустоту."""
    from app.main import LOOKUP_MIN_QUERY, SEARCH_MIN_QUERY

    _admin(client, db_session)
    assert LOOKUP_MIN_QUERY == 1, "порог подстановки поменялся — перечитай отчёт"
    assert SEARCH_MIN_QUERY == 2, "порог глобального поиска поменялся — перечитай отчёт"
    body = client.get("/orders/new")
    assert f"var MIN_QUERY = {LOOKUP_MIN_QUERY};" in body.text, (
        "клиентский порог разошёлся с серверным")


def test_the_lookup_answers_with_the_price_and_the_stock(client, db_session):
    _admin(client, db_session)
    product, _ = _seed(db_session)
    row = client.get("/api/lookup/products", params={"q": "Гвозди"}).json()["items"][0]
    assert row["id"] == product.id
    assert "Гвозди" in row["label"]
    assert row["hint"] == "100.00 ₽ · ост. 10"


def test_the_lookup_refuses_an_anonymous_visitor(client):
    assert client.get("/api/lookup/customers", params={"q": "ООО"}).status_code == 401


def test_the_lookup_keeps_its_rights_apart(client, db_session):
    role = Role(name="wholesale", is_system=False, is_super=False,
                permissions=["orders.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username="seller", password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "seller", PASSWORD).token)
    assert client.get("/api/lookup/products", params={"q": "Гвоз"}).status_code == 403
    assert client.get("/api/lookup/customers", params={"q": "ООО"}).status_code == 403


def test_the_order_form_shows_the_current_customer_past_the_first_page(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    db_session.add_all([Customer(name=f"{i:03d} заготовка") for i in range(26)])
    db_session.commit()
    order = _order(client, db_session, customer.id, product.id)
    block = _of(_pickers(client.get(f"/orders/{order}/edit").text), "customers", customer.id)
    assert block["text"]["value"] == "ООО Строй"
    assert block["attrs"]["data-lookup"] == "/api/lookup/customers"
    assert "name" not in block["text"], "видимое поле не должно уходить в форму"


def test_the_order_form_shows_the_current_product_past_the_first_page(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    _pad_products(client, 30)
    order = _order(client, db_session, customer.id, product.id)
    block = _of(_pickers(client.get(f"/orders/{order}/edit").text), "products", product.id)
    assert block["text"]["value"] == "Гвозди"
    assert block["attrs"]["data-lookup"] == "/api/lookup/products"


def test_a_deleted_customer_and_product_are_marked_in_the_picker(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    order = _order(client, db_session, customer.id, product.id)
    assert client.delete(f"/api/customers/{customer.id}").status_code == 204
    assert client.delete(f"/api/products/{product.id}").status_code == 204
    blocks = _pickers(client.get(f"/orders/{order}/edit").text)
    assert _of(blocks, "customers", customer.id)["text"]["value"] == "ООО Строй — удалён"
    assert _of(blocks, "products", product.id)["text"]["value"] == "Гвозди — удалён"


def test_the_order_form_no_longer_carries_a_list_of_entities(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    _pad_products(client, 30)
    for url in ["/orders/new", f"/orders/{_order(client, db_session, customer.id, product.id)}/edit"]:
        body = client.get(url).text
        assert '<select name="customer_id"' not in body
        assert '<select name="product_id"' not in body


def test_the_order_form_carries_the_picker_on_the_row_added_by_the_button(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    body = client.get("/orders/new").text
    template = body[body.index('<template id="item-row">'):body.index("</template>")]
    assert 'data-lookup="/api/lookup/products"' in template


def test_the_open_state_of_the_list_has_a_rule_of_its_own():
    """Список гасится и показывается одним правилом по `data-open`. Если его
    вычеркнуть из сборки, подстановка останется в форме, скрипт будет
    отвечать, а список не появится никогда — и ни один тест на HTML это не
    увидит. Поэтому проверяем артефакт, а не разметку."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text(
        encoding="utf-8")
    hidden = re.search(r"\.app-picker-list\{([^}]*)\}", css)
    assert hidden and "display:none" in hidden.group(1).replace(" ", ""), \
        "список не загашен по умолчанию"
    shown = re.search(r"\.app-picker\[data-open=true\][^{]*\{([^}]*)\}", css)
    assert shown and shown.group(1).strip(), "нет правила, открывающего список"


def test_the_picker_rules_reach_the_built_stylesheet_non_empty():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text(
        encoding="utf-8")
    import re
    for name in ("app-picker", "app-picker-list",
                 "app-picker-option", "app-picker-note"):
        rules = re.findall(r"\." + name + r"\{([^}]*)\}", css)
        assert rules, f"в app.css нет правила .{name}"
        assert any(body.strip() for body in rules), f"правило .{name} пустое"
    assert re.search(r"\.app-picker-option\[aria-selected=true\][^{]*\{[^}]+\}", css), (
        "нет правила для отмеченного пункта — стрелки не видно, куда они встали")


def _order(client, db_session, customer_id, product_id):
    r = client.post("/orders/new", data={
        "customer_id": str(customer_id), "discount": "0",
        "product_id": [str(product_id)], "quantity": ["1"], "line_discount": ["0"],
    }, follow_redirects=False)
    assert r.status_code == 303, r.headers.get("location")
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[1])


# --- Последние выбранные -----------------------------------------------------
#
# Таблица `orders` не помнит, кто создал заказ: колонки `user_id` у неё нет, а
# `create_order` принимает пользователя только для аудита. Поэтому «последние
# выбранные» — это последние заказы вообще, а не этого человека. Проверка ниже
# следит, чтобы завтра никто не выдумал принадлежность заказа: она требует, чтобы
# её НЕ было.


def test_the_orders_table_really_has_no_owner_so_nothing_can_filter_by_user():
    """Если бы у заказа появился `user_id`, «последние выбранные» можно было бы
    сузить до человека — но тогда это была бы другая задача, с другой миграцией.
    Пока колонки нет, любая попытка отфильтровать по пользователю была бы
    выдумкой, и тест обязан это показать."""
    from app.modules.orders.models import Order
    columns = {c.name for c in Order.__table__.columns}
    assert "user_id" not in columns, (
        "у заказа появился владелец — «последние выбранные» пора сузить до него "
        "и переписать это решение")


def test_an_empty_lookup_offers_the_customers_of_the_last_orders(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    other = Customer(name="ООО Второе")
    db_session.add(other)
    db_session.commit()
    _order(client, db_session, customer.id, product.id)
    _order(client, db_session, other.id, product.id)
    body = client.get("/api/lookup/customers").json()
    assert body["recent"] is True
    assert [row["label"] for row in body["items"]] == ["ООО Второе", "ООО Строй"], (
        "свежие должны быть первыми: последний заказ и есть самый свежий выбор")


def test_the_recent_customers_are_different_and_capped_at_ten(client, db_session):
    _admin(client, db_session)
    product, _ = _seed(db_session)
    ids = []
    for i in range(14):
        c = Customer(name=f"Клиент {i:02d}")
        db_session.add(c)
        db_session.flush()
        ids.append(c.id)
    db_session.commit()
    for cid in ids:
        _order(client, db_session, cid, product.id)
    body = client.get("/api/lookup/customers").json()
    labels = [row["label"] for row in body["items"]]
    assert len(labels) == 10, "потолок последних — десять, как и просили"
    assert len(set(labels)) == 10, "в списке повторы"
    assert labels == [f"Клиент {i:02d}" for i in range(13, 3, -1)], (
        f"порядок не «свежие первыми»: {labels}")


def test_the_recent_products_come_from_the_lines_of_the_last_orders(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    second = Product(name="Доска", sku="BOARD-1", unit_id=_unit_id(db_session),
                     price=5.0, rest=2)
    db_session.add(second)
    db_session.commit()
    _order(client, db_session, customer.id, product.id)
    _order(client, db_session, customer.id, second.id)
    labels = [row["label"] for row in
              client.get("/api/lookup/products").json()["items"]]
    assert labels == ["Доска", "Гвозди"]


def test_a_repeated_product_is_listed_once(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    _order(client, db_session, customer.id, product.id)
    r = client.post(f"/orders/new", data={
        "customer_id": str(customer.id), "discount": "0",
        "product_id": [str(product.id), str(product.id)],
        "quantity": ["1", "2"], "line_discount": ["0", "0"],
    }, follow_redirects=False)
    assert r.status_code == 303
    labels = [row["label"] for row in
              client.get("/api/lookup/products").json()["items"]]
    assert labels == ["Гвозди"], f"повтор в последних выбранных: {labels}"


def test_a_deleted_order_is_not_a_recent_choice(client, db_session):
    """Мягко удалённый заказ человек не выбирал: его клиент и товар не должны
    всплывать как «последние выбранные»."""
    _admin(client, db_session)
    product, customer = _seed(db_session)
    gone = Customer(name="ООО Ушедшее")
    db_session.add(gone)
    db_session.commit()
    keep = _order(client, db_session, customer.id, product.id)
    drop = _order(client, db_session, gone.id, product.id)
    assert client.delete(f"/api/orders/{drop}").status_code in (200, 204)
    labels = [row["label"] for row in
              client.get("/api/lookup/customers").json()["items"]]
    assert "ООО Ушедшее" not in labels, f"удалённый заказ попал в последние: {labels}"
    assert "ООО Строй" in labels
    assert keep != drop


def test_a_soft_deleted_entity_is_not_offered_as_recent(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    other = Customer(name="ООО Удалённое")
    db_session.add(other)
    db_session.commit()
    _order(client, db_session, customer.id, product.id)
    _order(client, db_session, other.id, product.id)
    assert client.delete(f"/api/customers/{other.id}").status_code == 204
    labels = [row["label"] for row in
              client.get("/api/lookup/customers").json()["items"]]
    assert "ООО Удалённое" not in labels, f"предложено удалить клиента: {labels}"


def test_a_short_query_is_still_a_search_and_not_the_recent_list(client, db_session):
    """Одна буква — это уже намерение искать, а не «покажи недавнее». Иначе первая
    же буква подменяла бы поиск списком того, что и так на руках."""
    _admin(client, db_session)
    product, customer = _seed(db_session)
    _order(client, db_session, customer.id, product.id)
    body = client.get("/api/lookup/customers", params={"q": "О"}).json()
    assert [row["label"] for row in body["items"]] == ["ООО Строй"]
    assert "recent" not in body, "короткий запрос отдал последние выбранные"


def test_the_recent_list_never_replaces_the_search_itself(client, db_session):
    _admin(client, db_session)
    product, customer = _seed(db_session)
    other = Customer(name="Ромашка")
    db_session.add(other)
    db_session.commit()
    _order(client, db_session, customer.id, product.id)
    body = client.get("/api/lookup/customers", params={"q": "Ромаш"}).json()
    assert [row["label"] for row in body["items"]] == ["Ромашка"]
    assert "recent" not in body


def test_the_recent_list_still_needs_the_rights(client, db_session):
    """Последние выбранные — это те же клиенты и товары, что и поиск, поэтому
    права не другие: без `customers.manage` список пуст, а не выдан."""
    assert client.get("/api/lookup/customers").status_code == 401
    assert client.get("/api/lookup/products").status_code == 401
    role = Role(name="clerk", is_system=False, is_super=False, permissions=["orders.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username="clerk", password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "clerk", PASSWORD).token)
    assert client.get("/api/lookup/customers").status_code == 403
    assert client.get("/api/lookup/products").status_code == 403


def test_the_form_carries_the_picker_that_asks_for_the_recent_list(client, db_session):
    """Список последних приходит по тому же адресу, что и поиск, — отдельного
    эндпоинта не заводим: второй путь запросов разошёлся бы с поиском."""
    _admin(client, db_session)
    body = client.get("/orders/new").text
    assert 'data-lookup="/api/lookup/customers"' in body
    assert 'data-lookup="/api/lookup/products"' in body
    assert "/api/recent" not in body
