"""Фильтр «Клиент» на списке заказов: писать имя, а не выбирать из неполного списка.

Слова владельца: «там похоже можно выбрать из определенного числа клиентов и
больше никак». Что было на самом деле: `<select name="customer_id">`, собранный из
`list_customers` с потолком страницы в 25 строк, то есть из первых 25 клиентов
из 995. Список не выбирал клиента, а показывал первую двадцатку и молчал.

Свободного текста, который ищет по имени клиента, на странице не было: `search` у
заказов смотрел только в номер. Поэтому честная правка — не второй механизм
рядом, а тот же поиск, доведённый до имени клиента, и снятие списка.

Живая фильтрация не должна пострадать: поле осталось тем же самым
(`input[name=search]` в форме с `data-live-filter`), то есть пауза, подстановка
списка и сохранение фокуса продолжают работать — это проверяет
`tests/test_live_filter_focus.py`, в том числе на самой странице заказов.
"""

from html.parser import HTMLParser
from pathlib import Path

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name
from app.modules.orders.models import Order
from app.modules.orders.service import create_order

PASSWORD = "password123"


def _admin(client, db_session):
    db_session.add(User(username="boss", password_hash=hash_password(PASSWORD),
                        role_id=get_role_by_name(db_session, "admin").id))
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _order_for(db_session, customer, product):
    return create_order(db_session, customer_id=customer.id, discount=0,
                        items=[{"product_id": product.id, "quantity": 1,
                                "discount": 0}])


def _product(db_session):
    unit_id = get_unit_by_name(db_session, "шт").id
    product = Product(name="Гвозди", sku="NAIL-1", unit_id=unit_id,
                      price=100.0, rest=100)
    db_session.add(product)
    db_session.commit()
    return product


class _Fields(HTMLParser):
    """Поля формы фильтра: подпись, имя, тип. Нужны, чтобы утверждать о форме, а не
    искать подстроку в её окрестностях."""

    def __init__(self):
        super().__init__()
        self.in_form = False
        self.fields = []
        self.selects = []
        self.last_label = ""
        self._label = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.in_form = "data-live-filter" in a
        elif self.in_form and tag == "label":
            self._label = []
        elif self.in_form and tag == "select":
            self.selects.append(a.get("name"))
        elif self.in_form and tag == "input":
            self.fields.append({"label": self.last_label,
                                "name": a.get("name"), "type": a.get("type", "text")})

    def handle_data(self, data):
        if self._label is not None:
            self._label.append(data)

    def handle_endtag(self, tag):
        if tag == "label" and self._label is not None:
            self.last_label = "".join(self._label).strip()
            self._label = None


def _filter_fields(body):
    parser = _Fields()
    parser.feed(body)
    return parser.fields


def _filter_selects(body):
    parser = _Fields()
    parser.feed(body)
    return parser.selects


def test_the_filter_form_offers_no_capped_list_of_clients(client, db_session):
    """Списка клиентов на странице больше нет вообще: у 995 клиентов любой список
    короче их всех, и человек искал бы несуществующего клиента."""
    _admin(client, db_session)
    product = _product(db_session)
    for i in range(30):
        customer = Customer(name=f"Клиент {i:03d}")
        db_session.add(customer)
        db_session.commit()
        _order_for(db_session, customer, product)
    body = client.get("/orders").text
    assert "customer_id" not in _filter_selects(body), (
        f"в форме фильтра остался выбор из закрытого списка: {_filter_selects(body)}")
    assert 'name="customer_id"' not in body, "поле выбора клиента осталось на странице"


def test_the_search_finds_an_order_by_the_client_name(client, db_session):
    _admin(client, db_session)
    product = _product(db_session)
    wanted = Customer(name="ООО Ромашка")
    other = Customer(name="ООО Весна")
    db_session.add_all([wanted, other])
    db_session.commit()
    hit = _order_for(db_session, wanted, product)
    miss = _order_for(db_session, other, product)
    body = client.get("/orders", params={"search": "Ромашка"}).text
    assert hit.number in body, "заказ по имени клиента не найден"
    assert miss.number not in body, "нашёлся чужой заказ"


def test_the_search_finds_a_client_from_beyond_the_first_page(client, db_session):
    """Ровно то, на что жаловался владелец: клиент двадцать шестой был
    недостижим — списка на странице не было, а имя не искалось."""
    _admin(client, db_session)
    product = _product(db_session)
    for i in range(25):
        db_session.add(Customer(name=f"Клиент {i:03d}"))
        db_session.commit()
    deep = Customer(name="ООО Глубокая Корпорация")
    db_session.add(deep)
    db_session.commit()
    order = _order_for(db_session, deep, product)
    assert order.number in client.get("/orders", params={"search": "Глубокая"}).text


def test_the_search_by_number_still_works(client, db_session):
    _admin(client, db_session)
    product = _product(db_session)
    customer = Customer(name="ООО Ромашка")
    db_session.add(customer)
    db_session.commit()
    order = _order_for(db_session, customer, product)
    assert order.number in client.get("/orders", params={"search": order.number}).text


def test_the_filter_field_says_that_it_takes_a_client_too(client, db_session):
    """Подпись обязана называть оба значения: иначе человек ищет по имени и не
    понимает, почему номер не находится."""
    _admin(client, db_session)
    field = next(f for f in _filter_fields(client.get("/orders").text)
                 if f["name"] == "search")
    assert "номер" in field["label"] and "клиент" in field["label"], field


def test_the_live_filter_still_has_one_box_and_it_is_the_search_ones(client, db_session):
    """Пауза и подстановка списка привязаны к `input[name=search]`. Второе текстовое
    поле потребовало бы своей паузы, а без неё фильтр работал бы лишь по кнопке —
    то есть тихо перестал бы фильтровать. Поэтому инвентарь текстовых полей
    заморожен: `search` и «Сумма от», которая по кнопке и была."""
    _admin(client, db_session)
    fields = _filter_fields(client.get("/orders").text)
    boxes = sorted(f["name"] for f in fields if f["type"] in ("text", "search"))
    assert boxes == ["min_amount", "search"], boxes
    base = (Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html")
    block = base.read_text(encoding="utf-8")
    assert 'form.querySelector("input[name=search]")' in block, (
        "живая фильтрация больше не привязана к полю поиска")


def test_the_orders_of_a_soft_deleted_client_are_still_found_by_name(client, db_session):
    """Мягко удалённый клиент не исчезает из заказов: заказ на странице есть, и
    найти его можно. Старый список suchал удалённых по галочке, а имя ищется
    всегда."""
    _admin(client, db_session)
    product = _product(db_session)
    customer = Customer(name="ООО Ромашка")
    db_session.add(customer)
    db_session.commit()
    order = _order_for(db_session, customer, product)
    assert client.delete(f"/api/customers/{customer.id}").status_code == 204
    assert order.number in client.get("/orders", params={"search": "Ромашка"}).text


def test_the_header_search_finds_the_order_by_the_client_too(client, db_session):
    """Поиск в шапке зовёт тот же `list_orders`, поэтому «Ромашка» находит и
    клиента, и его заказ. Расхождение было бы невозможно заметить."""
    _admin(client, db_session)
    product = _product(db_session)
    customer = Customer(name="ООО Ромашка")
    db_session.add(customer)
    db_session.commit()
    order = _order_for(db_session, customer, product)
    body = client.get("/search", params={"q": "Ромашка"}).text
    assert f'href="/orders/{order.id}"' in body, "заказ не нашёлся в шапке"
    assert f'href="/customers/{customer.id}"' in body


def test_the_api_still_takes_the_client_id(client, db_session):
    """`customer_id` у API и у карточки клиента остаётся: страница его больше не
    рисует, но `list_orders(db, customer_id=...)` зовёт и карточка клиента, и
    `/api/orders`."""
    _admin(client, db_session)
    product = _product(db_session)
    customer = Customer(name="ООО Ромашка")
    other = Customer(name="ООО Другая")
    db_session.add_all([customer, other])
    db_session.commit()
    _order_for(db_session, customer, product)
    wanted = _order_for(db_session, other, product)
    items = client.get("/api/orders", params={"customer_id": customer.id}).json()
    assert [row["id"] for row in items["items"]] == [db_session.query(Order)
                                                     .filter(Order.customer_id == customer.id)
                                                     .one().id]
    assert wanted.id not in [row["id"] for row in items["items"]]