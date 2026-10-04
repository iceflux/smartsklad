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

def test_a_query_shorter_than_two_characters_returns_nothing(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    body = client.get("/api/lookup/customers", params={"q": "О"}).json()
    assert body["items"] == []
    assert body["total"] == 0


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
