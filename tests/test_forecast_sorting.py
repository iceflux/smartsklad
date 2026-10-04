"""Сортировка таблицы прогноза по клику на заголовок.

Механизм взят тот же, что у трёх страниц разделов: белый список колонок раздела,
один вызов и обязательный второй ключ, иначе постраничный разбор дублирует и
теряет строки. Отличие одно и оно вынужденное: строки прогноза **считаются в
Python** (остаток, прогноз и рекомендация собираются из истории продаж), поэтому
`order_by` к ним неприменим — сортировка идёт по списку словарей, но граница
остаётся той же: ключ приходит от человека и попасть в порядок может только
через белый список.

Порядок по умолчанию не менялся: рекомендация по убыванию, затем имя. Иначе
страница при загрузке выглядела бы иначе, чем вчера.
"""

import re

from app.modules.ai.service import FORECAST_SORTS, _second_key, forecast_rows
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name
from app.sorting import apply_row_sort
from tests.test_ai_service import _daily_sales

PASSWORD = "password123"
HEADERS = ("Товар", "Артикул", "Остаток", "Запас", "Продажи/сутки",
           "7 дней", "14 дней", "30 дней", "К закупке")


def _admin(client, db_session):
    db_session.add(User(username="boss", password_hash=hash_password(PASSWORD),
                        role_id=get_role_by_name(db_session, "admin").id))
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _product(db_session, name, rest, price=10.0):
    product = Product(name=name, sku=f"SKU-{name}",
                      unit_id=get_unit_by_name(db_session, "шт").id,
                      price=price, rest=rest, min_stock=rest)
    db_session.add(product)
    db_session.commit()
    return product


def _customer(db_session):
    customer = Customer(name="ООО Ромашка")
    db_session.add(customer)
    db_session.commit()
    return customer


def _names(body):
    """Первые ячейки строк: по ним виден порядок, а не значения остальных
    колонок, которые в том же классе выглядят одинаково."""
    return re.findall(r'<tr class="border-b">\s*<td class="p-2">([^<]*)</td>', body)


def _sorted_links(body):
    """Ссылки сортировки в шапке: `ключ → (направление, подпись)`. Именно ссылки,
    а не текст ячеек: значение колонки и её заголовок выглядят одинаково."""
    return {key: (direction, label) for key, direction, label in re.findall(
        r'href="/forecast\?[^"]*sort=(\w+)&dir=(\w+)"[^>]*>([^<]*)<', body)}


def test_every_value_column_of_the_forecast_is_sortable(client, db_session):
    """На таблице прогноза нет колонок-действий: каждая колонка здесь — значение,
    и молчаливое исключение из сортировки было бы произволом. «НЕ сортируется
    Действия» — правило для страниц разделов, здесь оно неприменимо."""
    _admin(client, db_session)
    links = _sorted_links(client.get("/forecast").text)
    assert [links[key][1].strip() for key in links] == list(HEADERS), links


def test_the_allowlist_and_the_headers_agree(client, db_session):
    """Белый список выбирает и колонку, и порядок: подставить нечего, а
    расхождение с шапкой означало бы ссылку, которая сортирует не тем."""
    _admin(client, db_session)
    links = _sorted_links(client.get("/forecast").text)
    assert sorted(links) == sorted(FORECAST_SORTS), (
        f"шапка: {sorted(links)}, белый список: {sorted(FORECAST_SORTS)}")


def test_the_rows_follow_the_chosen_column_and_direction(client, db_session):
    _admin(client, db_session)
    for name, rest in (("А", 30), ("Б", 10), ("В", 20)):
        _product(db_session, name, rest)
    assert _names(client.get("/forecast?sort=rest&dir=asc").text) == ["Б", "В", "А"]
    assert _names(client.get("/forecast?sort=rest&dir=desc").text) == ["А", "В", "Б"]


def test_the_default_order_is_the_one_it_was_before(client, db_session):
    """Страница при загрузке обязана выглядеть как вчера: рекомендация по убыванию,
    затем имя. Товар без продаж в конце — у него рекомендации нет."""
    _admin(client, db_session)
    for name, rest in (("А", 30), ("Б", 10), ("В", 20)):
        _product(db_session, name, rest)
    _product(db_session, "Г", 0)
    assert _names(client.get("/forecast").text) == ["А", "Б", "В", "Г"]


def test_an_unknown_key_falls_back_instead_of_reaching_the_order(client, db_session):
    _admin(client, db_session)
    for name, rest in (("А", 30), ("Б", 10)):
        _product(db_session, name, rest)
    for hostile in ("rest; DROP TABLE products", "нет такого", "1"):
        r = client.get("/forecast", params={"sort": hostile, "dir": "asc"})
        assert r.status_code == 200, hostile
        assert _names(r.text) == ["А", "Б"], hostile


def test_the_purchase_filter_keeps_working_together_with_the_sort(client, db_session):
    """Фильтр «только к закупке» и сортировка не должны мешать друг другу: без
    фильтра строк две, с фильтром остаётся товар, который действительно закупают."""
    _admin(client, db_session)
    product = _product(db_session, "А", 30)
    customer = _customer(db_session)
    _daily_sales(db_session, product, customer, 20)
    _product(db_session, "Б", 0)
    body = client.get("/forecast", params={"only_purchase": "1", "sort": "rest",
                                           "dir": "asc"}).text
    assert _names(body) == ["А"], _names(body)
    assert "Товаров: 1" in body, "счётчик не сходится с отфильтрованным списком"


def test_the_paging_links_and_the_filter_keep_the_sort(client, db_session):
    _admin(client, db_session)
    for i in range(30):
        _product(db_session, f"Товар {i:02d}", 10)
    body = client.get("/forecast?sort=rest&dir=desc").text
    assert "sort=rest&amp;dir=desc" in body, "постраничная ссылка потеряла сортировку"
    assert 'name="sort" value="rest"' in body
    assert 'name="dir" value="desc"' in body


def test_the_sorted_column_shows_the_same_indicator_as_the_section_pages(client, db_session):
    _admin(client, db_session)
    _product(db_session, "А", 30)
    text = client.get("/forecast?sort=rest&dir=desc").text
    assert 'aria-sort="descending"' in text
    assert "▼" in text
    assert text.count("aria-sort") == 1, "отмечена не та колонка"
    assert "aria-sort" not in client.get("/forecast").text
    assert "▲" in client.get("/forecast?sort=rest&dir=asc").text


def test_equal_values_split_across_pages_neither_duplicated_nor_lost(client, db_session):
    """Страница не теряет и не дублирует строки на границе. Второй ключ проверяется
    отдельно — здесь он невидим: строки приходят в порядке товаров, и сортировка
    Python устойчива, то есть отсутствие второго ключа выглядело бы как порядок
    делом."""
    _admin(client, db_session)
    for i in range(30):
        _product(db_session, f"Товар {i:02d}", 10)
    seen = []
    for page in (1, 2):
        seen += _names(client.get(f"/forecast?sort=rest&dir=asc&page={page}").text)
    assert len(seen) == 30, seen
    assert len(set(seen)) == 30, sorted({name for name in seen if seen.count(name) > 1})


def test_the_second_key_orders_rows_that_are_equal_in_the_chosen_column():
    """Единственное место, где второй ключ виден: строки поданы не в
    том порядке, в каком должен быть результат. Без второго ключа устойчивая
    сортировка оставила бы их как попало, и постраничный разбор на границе страниц
    отдавал бы одну строку дважды, а другую не отдавал бы."""
    rows = [{"product_id": 3, "rest": 5}, {"product_id": 1, "rest": 5},
            {"product_id": 2, "rest": 5}]
    ordered = apply_row_sort(rows, FORECAST_SORTS, list, "rest", "asc", _second_key)
    assert [row["product_id"] for row in ordered] == [1, 2, 3]


def test_the_rows_without_a_forecast_do_not_break_the_ordering(db_session):
    """«Мало данных» — это прочерк в ячейке, а в порядке это ноль: иначе прочерк
    оказывался бы то выше, то ниже в зависимости от направления. Товаров без
    продаж здесь все, поэтому проверяется, что сортировка по прогнозу вовсе не
    падает и не теряет строки."""
    for name, rest in (("А", 30), ("Б", 10), ("В", 20)):
        _product(db_session, name, rest)
    # Все прогнозы — прочерки, то есть нули, поэтому порядок задаёт второй ключ:
    # сам товар. Без второго ключа строки с равными значениями переставлялись бы
    # как попало, и страница отдавала бы одну дважды.
    for direction in ("asc", "desc"):
        rows, total = forecast_rows(db_session, sort="forecast_7", direction=direction)
        assert total == 3, (direction, total)
        assert [row["name"] for row in rows] == ["А", "Б", "В"], direction
    rows, _ = forecast_rows(db_session, sort="recommended", direction="desc")
    assert [row["name"] for row in rows] == ["А", "Б", "В"]