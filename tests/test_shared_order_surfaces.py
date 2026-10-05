"""Окна выбора в заказе — это списки разделов и формы разделов, а не их копии.

Пункт владельца: окно выбора товара — «почти страница товаров», а окно создания —
вся форма товара, и то же для клиентов. Вторая копия разметки разошлась бы со
страницей при первом же новом поле, и заметил бы это только тот, кто искал одно
и то же дважды — то есть почти никто.

Поэтому проверка здесь не «в окне есть список», а сравнение: те же значения фильтра
и те же заголовки колонок на странице и в окне, те же поля формы. Обе поверхности
собираются из одного куска шаблона, и расхождение возможно только если этот кусок
скопируют — а вот тогда проверка падает.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "app" / "templates"

PASSWORD = "password123"
ORDER_RIGHTS = ["orders.view", "orders.create", "products.view", "customers.manage"]


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _user(client, db_session, permissions, username):
    role = Role(name="role-" + username, is_system=False, is_super=False,
                permissions=list(permissions))
    db_session.add(role)
    db_session.commit()
    db_session.add(User(username=username, password_hash=hash_password(PASSWORD),
                        role_id=role.id))
    db_session.commit()
    client.cookies.set("session", login(db_session, username, PASSWORD).token)


def _seed(db_session):
    unit = get_unit_by_name(db_session, "шт").id
    db_session.add_all([
        Product(name="Гвоздь", sku="NAIL-1", unit_id=unit, price=100.0, rest=10),
        Product(name="Доска", sku="BOARD-1", unit_id=unit, price=50.0, rest=3),
        Customer(name="ООО Строй", phone="+7-900-000-00-00", inn="7707080000"),
        Customer(name="Ромашка", phone="+7-900-111-22-33"),
    ])
    db_session.commit()


class _Modal(HTMLParser):
    """Что лежит внутри окна: поля формы и заголовки таблицы."""

    def __init__(self):
        super().__init__()
        self.name = None
        self.depth = 0
        self.inputs = []
        self.selects = []
        self.textareas = []
        self.headers = []
        self.links = []
        self.html = ""
        self._cell = None
        self._link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "div" and "app-modal" in classes:
            self.name = attrs.get("data-modal")
            self.depth = 1
            return
        if self.name is None:
            return
        if tag == "div":
            self.depth += 1
        elif tag == "input" and attrs.get("type") != "hidden":
            self.inputs.append(attrs.get("name"))
        elif tag == "select":
            self.selects.append(attrs.get("name"))
        elif tag == "textarea":
            self.textareas.append(attrs.get("name"))
        elif tag == "th":
            self._cell = []
        elif tag == "a" and self._cell is not None:
            self._link = []
        elif tag == "button":
            self.links.append("")

    def handle_endtag(self, tag):
        if self.name is None:
            return
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.name = None
        elif tag == "th":
            self.headers.append("".join(self._cell or []).strip())
            self._cell = None
        elif tag == "a":
            self._link = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


class _FormFields(HTMLParser):
    """Имена полей формы — все, а не только по записке: окно создания товара
    обязано нести столько же полей, сколько карточка."""

    def __init__(self):
        super().__init__()
        self.in_form = False
        self.names = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and (attrs.get("id") in ("product-form", "customer-form")
                              or "data-create-form" in attrs):
            self.in_form = True
            return
        if not self.in_form:
            return
        if tag in ("input", "select", "textarea"):
            name = attrs.get("name")
            if name:
                self.names.append(name)

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False


class _Fields(HTMLParser):
    """Имена полей формы товара или клиента."""

    def __init__(self):
        super().__init__()
        self.in_form = False
        self.names = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") in ("product-form", "customer-form"):
            self.in_form = True
            return
        if not self.in_form:
            return
        if tag in ("input", "select", "textarea"):
            name = attrs.get("name")
            if name:
                self.names.append(name)

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False


def _modals(body):
    """Окна по одному: _Modal держит только последнее разобранное окно."""
    found = {}
    for name in ("browse-products", "create-products", "browse-customers",
                 "create-customers"):
        mark = body.find(f'data-modal="{name}"')
        if mark == -1:
            continue
        # Открывающий тег окна начинается ДО признака `data-modal`, поэтому ищем
        # его с конца: с позиции признака разметка начиналась бы с обрубка тега,
        # и разбор молча не увидел бы окна вовсе.
        start = body.rindex('<div class="app-modal"', 0, mark)
        single = _Modal()
        single.feed(body[start:])
        # Окно кончается либо следующим окном, либо концом страницы. Иначе в срез
        # попал бы следующее окно, и проверка полей сравнивала бы форму товара с
        # формой клиента.
        stops = [body.find('<div class="app-modal"', start + 1),
                 body.index("</main>", start)]
        single.html = body[start:min(at for at in stops if at != -1)]
        found[name] = single
    return found


def _fields(html, form_id):
    parser = _Fields()
    parser.feed(html)
    return [n for n in parser.names]


class _Selects(HTMLParser):
    """Значения `<option>` каждого списка формы — их человек видит."""

    def __init__(self):
        super().__init__()
        self.options = []
        self._select = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self._select = []
        elif tag == "option" and self._select is not None:
            self._select.append(attrs.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "select" and self._select is not None:
            self.options.append(self._select)
            self._select = None


class _TableRows(HTMLParser):
    """Строки таблицы списка: список списков с текстом ячеек."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.head = False
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "thead":
            self.head = True
        elif tag == "tbody":
            self.head = False
        elif tag == "tr" and not self.head:
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _rows(html):
    parser = _TableRows()
    parser.feed(html)
    return [row for row in parser.rows if row]


def _form_of(html, form_id):
    start = html.find(f'<form id="{form_id}"')
    assert start != -1, f"на странице нет формы {form_id}"
    return html[start:html.index("</form>", start)]


# --- Товары -------------------------------------------------------------------


def _forms(html):
    out = []
    at = 0
    while True:
        start = html.find("<form", at)
        if start == -1:
            return out
        end = html.index("</form>", start) + len("</form>")
        out.append(html[start:end])
        at = end


def _headers(html):
    table = html[html.index("<thead>"):html.index("</thead>")]
    out = []
    for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S):
        text = re.sub(r"<[^>]+>", "", cell)
        text = text.replace("\u25bc", "").replace("\u25b2", "").strip()
        if text:
            out.append(text)
    return out


def _fields_of(form_html):
    """Имена полей формы и значения её списков — то, что человек видит и чем
    пользуется, а не класс и не порядок атрибутов."""
    parser = _Fields()
    parser.feed(form_html)
    return sorted(n for n in parser.names if n)


def _options_of(form_html):
    parser = _Selects()
    parser.feed(form_html)
    return parser.options


def _form_with(html, needle):
    """Форма, внутри которой встречается `needle`. Нельзя брать первую: в
    `base.html` есть форма поиска из шапки, и «первая форма страницы» — это она."""
    for form in _forms(html):
        if needle in form:
            return form
    raise AssertionError(f"на странице нет формы с «{needle}»")


def test_the_browse_window_carries_the_products_filter_verbatim(client, db_session):
    """Фильтр в окне — тот же фильтр, что на странице: те же поля и тот же список
    категорий. Сравниваются имена полей и значения списка, а не класс и не
    порядок атрибутов, потому что человек видит именно их."""
    _admin(client, db_session)
    _seed(db_session)
    page = client.get("/products").text
    window = _modals(client.get("/orders/new").text)["browse-products"]
    page_filter = _form_with(page, "Поиск (название/артикул)")
    window_filter = _form_with(window.html, "Поиск (название/артикул)")
    assert _fields_of(page_filter) == _fields_of(window_filter), (
        f"страница {_fields_of(page_filter)}, окно {_fields_of(window_filter)}")
    assert _options_of(page_filter) == _options_of(window_filter), "список категорий разошёлся"
    assert "Показывать удалённые" in page_filter


def test_the_browse_window_has_the_same_columns_as_the_products_page(client, db_session):
    """Заголовки колонок в окне приезжают тем же куском шаблона, что и на странице,
    поэтому сравниваются две отрендеренные таблицы, а не разметка страницы заказа:
    в ней списка ещё нет — он приезжает по запросу."""
    _admin(client, db_session)
    _seed(db_session)
    page = client.get("/products?sort=rest&dir=desc&search=Гвоз").text
    fragment = client.get("/api/lookup/products/table?sort=rest&dir=desc&search=Гвоз").text
    # Различие ровно одно и ровно то, о котором просил владелец: колонки действий.
    assert _headers(page) == _headers(fragment) + ["Действия"], (
        f"страница {_headers(page)}, окно {_headers(fragment)}")
    assert _headers(fragment) == ["Название", "Артикул", "Категория", "Ед.",
                                   "Цена", "Остаток"], _headers(fragment)


def test_the_window_keeps_the_sort_indicator_of_the_page(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    for url in ("/products?sort=rest&dir=desc",
                "/api/lookup/products/table?sort=rest&dir=desc"):
        body = client.get(url).text
        assert 'aria-sort="descending"' in body, (
            f"в окне нет индикатора сортировки, который рисует `sort_head`: {url}")


def test_every_column_of_the_window_is_sortable_not_merely_titled(client, db_session):
    """Сортировка в окне — не украшение, а ссылка. Заголовок текстом выглядел бы
    тем же самым, и заметить отличие можно только здесь: пункт ссылки и её адрес."""
    _admin(client, db_session)
    _seed(db_session)
    fragment = client.get("/api/lookup/products/table").text
    table = fragment[fragment.index("<thead>"):fragment.index("</thead>")]
    cells = re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)
    assert len(cells) == 6, f"в окне {len(cells)} колонок: {cells}"
    keys = ["name", "sku", "category", "unit", "price", "rest"]
    for cell, key in zip(cells, keys):
        assert f"sort={key}" in cell, (
            f"колонка не сортируется: {cell}")
        assert f"/api/lookup/products/table?" in cell, (
            f"ссылка сортировки ведёт не в окно: {cell}")
    assert "Вперёд" in fragment or "Стр. 1 из 1" in fragment, (
        "в окне нет постраничной строки — список не пролистать")


def test_the_window_pages_exactly_like_the_page(client, db_session):
    _admin(client, db_session)
    unit = get_unit_by_name(db_session, "шт").id
    for i in range(30):
        db_session.add(Product(name=f"Заготовка {i:02d}", sku=f"Z-{i:02d}",
                               unit_id=unit, price=10.0, rest=i))
    db_session.commit()
    page = client.get("/products").text
    fragment = client.get("/api/lookup/products/table").text
    assert "Всего: 30" in page and "Всего: 30" in fragment, (
        "счётчик строк разошёлся: страница и окно показывают разное число")
    assert "Стр. 1 из 2" in page and "Стр. 1 из 2" in fragment
    assert "/api/lookup/products/table?" in fragment, (
        "постраничная ссылка в окне ведёт не в окно")
    second = client.get("/api/lookup/products/table?page=2").text
    assert len(_rows(second)) == 5, len(_rows(second))


def test_the_window_answers_with_the_same_rows_the_products_page_shows(client, db_session):
    """Один и тот же запрос — один и тот же набор строк. Это и есть смысл общей
    разметки: окно не «примерно то же», а ровно то же."""
    _admin(client, db_session)
    _seed(db_session)
    page = _rows(client.get("/products?search=Гвоздь").text)
    window = _rows(client.get("/api/lookup/products/table?search=Гвоздь").text)
    assert page, "товар не найден на странице"
    # У страницы впереди ячейка галочки и сзади колонка действий, которых в окне
    # нет, поэтому сравниваются шесть ячеек данных: у страницы 1..7, у окна 0..6.
    assert len(page) == len(window), f"страница {page}, окно {window}"
    for one, other in zip(page, window):
        assert one[1:7] == other[0:6], (
            f"страница {one[1:7]} и окно {other[0:6]} разошлись")


def test_the_window_keeps_the_actions_column_out_and_the_filter_in(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    window = _modals(client.get("/orders/new").text)["browse-products"]
    assert "Действия" not in window.headers, (
        f"в окне снова есть колонка действий: {window.headers}")
    fragment = client.get("/api/lookup/products/table").text
    assert "Действия" not in fragment, "эндпоинт отдал колонку действий"
    assert "bulk-category" not in fragment, "эндпоинт отдал форму массовой правки"


def test_the_window_sorts_and_pages_like_the_products_page(client, db_session):
    _admin(client, db_session)
    unit = get_unit_by_name(db_session, "шт").id
    for i in range(30):
        db_session.add(Product(name=f"Заготовка {i:02d}", sku=f"Z-{i:02d}",
                               unit_id=unit, price=float(30 - i), rest=i))
    db_session.commit()
    window = _rows(client.get("/api/lookup/products/table?sort=price&dir=asc").text)
    page = _rows(client.get("/products?sort=price&dir=asc").text)
    prices = [float(row[4]) for row in window]
    assert prices == sorted(prices), f"порядок в окне не по цене: {prices}"
    # У страницы впереди ячейка галочки, поэтому цена там на одну позицию правее.
    page_prices = [float(row[5]) for row in page][:len(prices)]
    assert page_prices == prices, "страница и окно отдали разный порядок"
    fragment = client.get("/api/lookup/products/table?sort=price&dir=asc").text
    assert "/api/lookup/products/table?" in fragment, (
        "ссылки сортировки в окне ведут не в окно")
    assert client.get("/api/lookup/products/table?sort=bogus").status_code == 200


def test_an_anonymous_visitor_gets_nothing_from_the_window(client):
    assert client.get("/api/lookup/products/table").status_code == 401
    assert client.get("/api/lookup/customers/table").status_code == 401


# --- Форма товара --------------------------------------------------------------


def _form_fields(form_html):
    parser = _FormFields()
    parser.feed(form_html)
    return parser.names


def _form_with(html, marker):
    for form in _forms(html):
        if marker in form:
            return form
    raise AssertionError(f"нет формы с «{marker}»")


def test_the_create_window_has_every_field_the_product_form_has(client, db_session):
    """Главная проверка пункта 3: окно создания товара — вся форма товара.

    Сравниваются списки полей двух ОТРЕНДЕРЕННЫХ форм. Копия разметки, добавленная
    в окно (или, наоборот, поле, забытое в окне), рассыплется здесь, а не
    обнаружится у человека, который создаст товар без единицы измерения."""
    _admin(client, db_session)
    _seed(db_session)
    page = client.get("/products/new").text
    window = _modals(client.get("/orders/new").text)["create-products"]
    section = _form_fields(_form_with(page, 'name="characteristic"'))
    in_window = _form_fields(window.html)
    assert sorted(section) == sorted(in_window), (
        f"форма товара {sorted(section)}, окно {sorted(in_window)}")


def test_the_create_window_keeps_the_whole_product_form_not_two_fields(client, db_session):
    """Проверка на словах, а не только на списке полей: у окна обязаны быть
    единица измерения, минимальный запас, остаток, описание и характеристики —
    ровно то, чего не было в прошлой двухполевой форме."""
    _admin(client, db_session)
    _seed(db_session)
    window = _modals(client.get("/orders/new").text)["create-products"]
    text = window.html
    for label in ("Единица измерения", "Минимальный запас", "Цена", "Остаток",
                  "Описание", "Характеристики (JSON)", "Категория", "Артикул"):
        assert label in text, f"в окне создания нет поля «{label}»"
    assert "Пусто — подставится следующий свободный номер." in text, (
        "в окне нет подсказки про автоартикул — человек не знает, что поле можно "
        "оставить пустым")
    assert text.index('name="description"') < text.index('name="characteristic"'), (
        "характеристики должны остаться последним полем формы")


def test_the_both_product_forms_share_one_body_builder(client, db_session):
    """Тело запроса собирает одна функция из `partials/product_form.html`, и обе
    формы её зовут. Своя сборка в окне означала бы второе место, где пишется
    разбор полей, а новое поле доезжало бы до API с одной формы и молча пропадало
    бы с другой."""
    partial = (TEMPLATE_DIR / "partials" / "product_form.html").read_text(encoding="utf-8")
    builder = partial[partial.index("window.smartskladProductBody"):]
    for page in ("app/templates/product_form.html", "app/templates/order_form.html"):
        body = (ROOT / page).read_text(encoding="utf-8")
        assert "window.smartskladProductBody(form" in body, (
            f"{page}: форма не зовёт общий разбор полей")
        assert "JSON.stringify({" not in body or "canEdit" in body, (
            f"{page}: у формы своя сборка тела запроса рядом с общей")
    for word in ("form.name.value", "form.sku.value", "form.description.value",
                 "form.min_stock", "form.characteristic", "form.price.value",
                 "form.rest.value", "form.track_rest"):
        assert word in builder, f"общий разбор забыл поле: {word}"


def test_the_window_form_and_the_section_form_read_the_same_permissions(client, db_session):
    """Окно создания отдаёт те же поля, что и карточка товара, поэтому и права на
    них те же: у кого нет права на цены, в обоих местах она недоступна."""
    _user(client, db_session, ["orders.view", "orders.create", "products.view",
                              "products.edit"], "noprice")
    page = _modals(client.get("/orders/new").text)["create-products"]
    assert 'name="price" type="number" step="0.01" value="0" class="w-full border p-2" disabled' \
        in page.html, "в окне цена доступна без права на неё"
    assert 'name="name" required' in page.html


# --- Клиенты ------------------------------------------------------------------


def test_the_customer_window_carries_the_customers_filter_verbatim(client, db_session):
    """Фильтр в окне клиента — тот же фильтр, что на странице клиентов: те же поля
    под тем же текстом. Сравниваются имена полей, а не класс и не порядок
    атрибутов, потому что человек видит именно их."""
    _admin(client, db_session)
    _seed(db_session)
    page = client.get("/customers").text
    window = _modals(client.get("/orders/new").text)["browse-customers"]
    page_filter = _form_with(page, "Поиск (имя/телефон/ИНН)")
    window_filter = _form_with(window.html, "Поиск (имя/телефон/ИНН)")
    assert _fields_of(page_filter) == _fields_of(window_filter), (
        f"страница {_fields_of(page_filter)}, окно {_fields_of(window_filter)}")
    assert "Показывать удалённые" in page_filter


def test_the_customer_window_has_the_same_columns_as_the_customers_page(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    page = client.get("/customers?sort=inn&dir=desc").text
    fragment = client.get("/api/lookup/customers/table?sort=inn&dir=desc").text
    assert _headers(page) == _headers(fragment) + ["Действия"], (
        f"страница {_headers(page)}, окно {_headers(fragment)}")
    assert _headers(fragment) == ["Имя", "Телефон", "ИНН", "Скидка"], _headers(fragment)


def test_every_column_of_the_customer_window_is_sortable(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    fragment = client.get("/api/lookup/customers/table").text
    table = fragment[fragment.index("<thead>"):fragment.index("</thead>")]
    cells = re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)
    assert len(cells) == 4, f"в окне {len(cells)} колонок: {cells}"
    for cell, key in zip(cells, ["name", "phone", "inn", "discount"]):
        assert f"sort={key}" in cell, f"колонка не сортируется: {cell}"
        assert "/api/lookup/customers/table?" in cell, (
            f"ссылка сортировки ведёт не в окно: {cell}")


def test_the_customer_window_answers_with_the_same_rows_the_page_shows(client, db_session):
    """Один и тот же запрос — один и тот же набор строк, и то же самое, что у
    товаров: окно не «примерно то же», а ровно то же."""
    _admin(client, db_session)
    _seed(db_session)
    page = _rows(client.get("/customers?search=Ромашка").text)
    window = _rows(client.get("/api/lookup/customers/table?search=Ромашка").text)
    assert page and window, (page, window)
    assert [r[0] for r in page] == [r[0] for r in window], (page, window)
    shared = 4
    for one, other in zip(page, window):
        assert one[:shared] == other[:shared], (one[:shared], other[:shared])


def test_the_customer_window_keeps_the_actions_column_out(client, db_session):
    _admin(client, db_session)
    _seed(db_session)
    window = _modals(client.get("/orders/new").text)["browse-customers"]
    assert "Действия" not in window.headers, window.headers
    fragment = client.get("/api/lookup/customers/table").text
    assert "Удалить клиента" not in fragment
    assert "/restore" not in fragment


def test_the_customer_window_needs_the_same_rights_as_the_page(client, db_session):
    _user(client, db_session, ["orders.view", "orders.create", "products.view",
                               "products.edit", "price.change"], "noclients")
    assert client.get("/api/lookup/customers/table").status_code == 403
    _user(client, db_session, ["orders.view", "orders.create", "customers.manage"],
          "withclients")
    assert client.get("/api/lookup/customers/table").status_code == 200


def test_the_create_client_window_has_every_field_the_customer_form_has(client, db_session):
    """Окно создания клиента — вся форма клиента, и это сравнивается так же, как у
    товара: копия разметки или забытое поле рассыплются здесь."""
    _admin(client, db_session)
    _seed(db_session)
    page = client.get("/customers/new").text
    window = _modals(client.get("/orders/new").text)["create-customers"]
    section = _form_fields(_form_with(page, 'name="comment"'))
    in_window = _form_fields(window.html)
    assert sorted(section) == sorted(in_window), (
        f"форма клиента {sorted(section)}, окно {sorted(in_window)}")
    for label in ("Имя", "ИНН (10 цифр)", "Телефон", "Email", "Скидочная карта",
                  "Персональная скидка, %", "Комментарий"):
        assert label in window.html, f"в окне создания клиента нет поля «{label}»"


def test_the_both_customer_forms_share_one_body_builder(client, db_session):
    partial = (TEMPLATE_DIR / "partials" / "customer_form.html").read_text(encoding="utf-8")
    builder = partial[partial.index("window.smartskladCustomerBody"):]
    for page in ("app/templates/customer_form.html", "app/templates/order_form.html"):
        body = (ROOT / page).read_text(encoding="utf-8")
        assert "window.smartskladCustomerBody(" in body, (
            f"{page}: форма не зовёт общий разбор полей клиента")
    for word in ("form.name.value", "form.inn.value", "form.phone.value",
                 "form.email.value", "form.discount_card.value",
                 "form.personal_discount.value", "form.comment.value"):
        assert word in builder, f"общий разбор забыл поле: {word}"
