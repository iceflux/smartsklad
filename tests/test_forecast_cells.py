"""Что стоит в ячейках таблицы прогноза, когда прогноза нет.

Владелец не понял прочерк в колонке «К закупке»: он не читается ни как «не
закупать», ни как «данных мало». Прочерк значил сразу три разные вещи — по
одному на колонку, — и соседние колонки говорили одинаковом разными словами.

Проверяется здесь не текст, а состояние ячейки: у каждого из трёх горизонтов
своё условие, и оно не то же самое, что у «К закупке». Отдельно проверяется
равенство `forecast_h is None` и `not has_data` — на нём держится короткая
ветка в шаблоне, и еслиservice когда-нибудь перестанет их совпадать, страница
покажет «мало данных» строке с прогнозом, и заметить это, глядя на таблицу,
нельзя.
"""

import random
import re

from app.modules.ai.service import (
    MIN_DAYS_FOR_FORECAST,
    _linear_forecast,
    forecast_rows,
)
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name
from tests.test_ai_service import _daily_sales

PASSWORD = "password123"

"""Прочерк в таблице прогноза больше не встречается: он значил сразу три разные
вещи и не читался ни одну из них."""
DASH = "—"

LITTLE_DATA = "мало данных"
NOT_NEEDED = "не нужно"


def _admin(client, db_session):
    db_session.add(User(username="boss", password_hash=hash_password(PASSWORD),
                        role_id=get_role_by_name(db_session, "admin").id))
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _customer(db_session):
    customer = Customer(name="ООО Ромашка")
    db_session.add(customer)
    db_session.commit()
    return customer


def _product(db_session, sku, rest):
    product = Product(name=f"Товар {sku}", sku=sku,
                      unit_id=get_unit_by_name(db_session, "шт").id,
                      price=10.0, rest=rest)
    db_session.add(product)
    db_session.commit()
    return product


def _with_sales(db_session, sku, rest=0, days=20):
    product = _product(db_session, sku, rest)
    _daily_sales(db_session, product, _customer(db_session), days)
    return product


def _cells(body, sku):
    """Ячейки строки по артикулу, без тегов. Читается именно строка, а не
    страница: значения соседних колонок у строк с прогнозом выглядят
    одинаково, и проверка «где-то на странице есть» прошла бы не по тому
    поводу."""
    row = re.search(r'<tr class="border-b">((?:\s*<td[^>]*>.*?</td>){9})', body)
    assert row, "в таблице прогноза не найдено строк"
    blocks = re.findall(r"<td[^>]*>(.*?)</td>", row.group(1), re.S)
    assert len(blocks) == 9, blocks
    assert sku in blocks[1], f"первая строка — не {sku}: {blocks[1]!r}"
    return [re.sub(r"<[^>]+>", "", block).strip() for block in blocks]


def _only_row(client, **params):
    body = client.get("/forecast", params=params).text
    assert body.count('<tr class="border-b">') == 2, "в таблице не одна строка"
    return body


def test_the_seven_day_cell_says_little_data_when_there_is_no_data(client, db_session):
    _admin(client, db_session)
    _with_sales(db_session, "SKU-FEW", days=3)
    cells = _cells(client.get("/forecast").text, "SKU-FEW")
    assert cells[5] == LITTLE_DATA, cells[5]


def test_the_fourteen_and_thirty_day_cells_say_the_same_as_the_seven_day_one(
        client, db_session):
    """Три соседние колонки об одном и том же не могут говорить разными
    словами: прежде колонка «7 дней» говорила «мало данных», а две соседние —
    прочерк, и это выглядело как три разных состояния."""
    _admin(client, db_session)
    _with_sales(db_session, "SKU-FEW", days=3)
    cells = _cells(client.get("/forecast").text, "SKU-FEW")
    assert [cells[5], cells[6], cells[7]] == [LITTLE_DATA] * 3, cells[5:8]


def test_the_three_horizon_cells_carry_the_numbers_when_there_is_data(client, db_session):
    _admin(client, db_session)
    _with_sales(db_session, "SKU-MANY", days=20)
    cells = _cells(client.get("/forecast").text, "SKU-MANY")
    rows, _ = forecast_rows(db_session)
    expected = [str(rows[0][f"forecast_{h}"]) for h in (7, 14, 30)]
    assert [str(int(value)) for value in cells[5:8]] == expected, (cells[5:8], expected)


def test_the_purchase_cell_says_not_needed_when_the_stock_covers_the_demand(
        client, db_session):
    _admin(client, db_session)
    _with_sales(db_session, "SKU-ENOUGH", rest=100000)
    cells = _cells(client.get("/forecast").text, "SKU-ENOUGH")
    assert cells[8] == NOT_NEEDED, cells[8]


def test_the_purchase_cell_does_not_say_not_needed_when_it_cannot_tell(client, db_session):
    """«Не нужно» — это решение, а не отсутствие ответа. У строки без данных
    решения нет: приложение не гадает. Слово «не нужно» здесь было бы прямой
    ложью и хуже прочерка — человек закрыл бы вопрос и не закупил."""
    _admin(client, db_session)
    _with_sales(db_session, "SKU-FEW", days=3)
    cells = _cells(client.get("/forecast").text, "SKU-FEW")
    assert cells[8] == LITTLE_DATA, cells[8]
    assert cells[8] != NOT_NEEDED


def test_a_purchase_row_still_shows_its_number_in_bold(client, db_session):
    """Число закупки остаётся тем же живым числом: причина правки — читаемость
    отсутствия, а не самого решения."""
    _admin(client, db_session)
    _with_sales(db_session, "SKU-NEED", rest=0)
    body = client.get("/forecast").text
    row = re.search(r'<tr class="border-b">((?:\s*<td[^>]*>.*?</td>){9})', body).group(1)
    bold = re.findall(r'<span class="font-bold">([^<]*)</span>', row)
    assert len(bold) == 1, f"жирным должен быть ровно один столбец — «К закупке»: {bold}"
    assert bold[0].strip() not in ("", DASH), bold


def test_the_forecast_table_has_no_dash_left(client, db_session):
    _admin(client, db_session)
    _with_sales(db_session, "SKU-MANY", rest=0)
    _with_sales(db_session, "SKU-ENOUGH", rest=100000)
    _with_sales(db_session, "SKU-FEW", days=3)
    body = client.get("/forecast").text
    table = body[body.index("<table"):body.index("</table>")]
    assert DASH not in table, "прочерк вернулся в таблицу прогноза"


def test_sorting_by_purchase_still_puts_the_purchases_on_top(client, db_session):
    _admin(client, db_session)
    _with_sales(db_session, "SKU-NEED", rest=0)
    _with_sales(db_session, "SKU-ENOUGH", rest=100000)
    for direction in ("desc", "asc"):
        r = client.get("/forecast", params={"sort": "buy", "dir": direction})
        assert r.status_code == 200, direction
        cells = _cells(r.text, "SKU-NEED" if direction == "desc" else "SKU-ENOUGH")
        assert cells[8] != LITTLE_DATA, (
            "при сортировке по «К закупке» строка без решения потеряла его")


def test_sorting_by_purchase_over_rows_without_a_forecast_does_not_fail(client, db_session):
    """В порядке прочерк — это ноль, иначе строки «мало данных» оказывались бы
    то выше, то ниже в зависимости от направления. Правка шаблона сортировку
    не трогала, и проверка стоит на странице, а не только на сервисе."""
    _admin(client, db_session)
    _with_sales(db_session, "SKU-NEED", rest=0)
    _with_sales(db_session, "SKU-FEW", days=3)
    for direction in ("asc", "desc"):
        r = client.get("/forecast", params={"sort": "buy", "dir": direction})
        assert r.status_code == 200, direction
        assert "SKU-FEW" in r.text, direction


def test_the_purchase_filter_still_excludes_the_rows_that_need_nothing(client, db_session):
    _admin(client, db_session)
    _with_sales(db_session, "SKU-NEED", rest=0)
    _with_sales(db_session, "SKU-ENOUGH", rest=100000)
    _with_sales(db_session, "SKU-FEW", days=3)
    body = _only_row(client, only_purchase="1")
    assert "SKU-NEED" in body
    assert "SKU-ENOUGH" not in body, "«не нужно» попало в «только к закупке»"
    assert "SKU-FEW" not in body, "строка без решения попала в «только к закупке»"


def test_forecast_is_none_exactly_when_there_is_no_data(db_session):
    """Равенство, на котором держится короткая ветка шаблона: у строки с
    данными прогноз не может оказаться пустым, а у строки без данных он не
    может быть посчитан. Проверяется перебором, а не рассуждением: при
    `has_data` нужно не меньше MIN_DAYS_FOR_FORECAST дней с продажами, то есть
    ряд длиннее единицы, а `_linear_forecast` возвращает None только на ряде
    короче двух или на ряде с нулевым знаменателем — и то и другое при такой
    длине невозможно."""
    random.seed(7)
    for length in range(0, 95):
        for _ in range(40):
            series = [random.choice([0, 0, 1, 2, 7, 90]) for _ in range(length)]
            has_data = sum(1 for q in series if q > 0) >= MIN_DAYS_FOR_FORECAST
            if not has_data:
                continue
            for horizon in (7, 14, 30):
                assert _linear_forecast(series, horizon) is not None, (
                    length, horizon, has_data)


def test_the_equivalence_also_holds_on_rows_the_service_actually_returns(db_session):
    customer = _customer(db_session)
    for index, (sku, days) in enumerate((("SKU-A", 20), ("SKU-B", 13), ("SKU-C", 0))):
        product = _product(db_session, sku, rest=index)
        if days:
            _daily_sales(db_session, product, customer, days,
                         start_number=1 + index * 20)
    rows, _ = forecast_rows(db_session)
    for row in rows:
        for horizon in (7, 14, 30):
            assert (row[f"forecast_{horizon}"] is None) is (not row["has_data"]), row


def test_the_equivalence_is_what_lets_the_three_cells_share_one_condition(client, db_session):
    """Сторож самого решения, а не его результата: если равенство когда-нибудь
    разойдётся, страница начнёт писать «мало данных» строке, у которой прогноз
    есть, и ни один тест, читающий текст страницы, этого не заметит — у обеих
    строк в таблице будет написано одно и то же."""
    _admin(client, db_session)
    _with_sales(db_session, "SKU-MANY", days=20)
    _with_sales(db_session, "SKU-FEW", days=3)
    body = client.get("/forecast").text
    rows, _ = forecast_rows(db_session)
    by_sku = {row["sku"]: row for row in rows}
    for row in re.findall(r'<tr class="border-b">((?:\s*<td[^>]*>.*?</td>){9})', body):
        blocks = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        sku = re.sub(r"<[^>]+>", "", blocks[1]).strip()
        has_data = by_sku[sku]["has_data"]
        for index in (5, 6, 7):
            assert (LITTLE_DATA in blocks[index]) is (not has_data), (sku, index)


def test_the_rows_keep_the_same_data_when_a_forecast_is_missing(client, db_session):
    """Строка без прогноза всё равно показывает остаток, запас и продажи в
    сутки: «мало данных» относится к прогнозу, а не к товару, и пустые эти
    три колонки уводили бы в мысль, что товара нет."""
    _admin(client, db_session)
    product = _with_sales(db_session, "SKU-FEW", rest=7, days=3)
    cells = _cells(client.get("/forecast").text, "SKU-FEW")
    assert cells[2].startswith("7"), cells[2]
    rows, _ = forecast_rows(db_session)
    row = rows[0]
    assert row["product_id"] == product.id
    assert row["min_stock"] > 0
    assert row["avg_daily_sold"] > 0
