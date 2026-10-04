"""Сортировка по колонкам, значение которых приходит из связи.

«Клиент» у заказа, «Категория» и «Ед.» у товара нарисованы не своей колонкой, а
именем из другой таблицы. Раньше сортировки по ним не было, и отказ был
формулировкой «это связь, нужен JOIN» — а JOIN это работа, а не запрет.

Три требования, и каждое проверяется отдельно:

* **Соединение внешнее.** `products.category_id` nullable, и внутреннее
  присоединение убрало бы товар без категории со страницы целиком. Для человека
  это выглядит как «товар потерялся».
* **Второй ключ `id` остаётся.** Без него равные по колонке строки не имеют
  порядка, и `OFFSET` рвёт постраничный разбор.
* **Ключ по-прежнему из белого списка.** Он выбирает теперь не только колонку, но
  и присоединение, поэтому и то и другое берётся из словаря раздела.
"""

import re

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Category, Product
from app.modules.inventory.service import (
    DEFAULT_PRODUCT_DIRECTION,
    DEFAULT_PRODUCT_SORT,
    PRODUCT_JOINS,
    PRODUCT_SORTS,
    get_unit_by_name,
    list_products,
)
from app.modules.orders.models import Order
from app.modules.orders.service import (
    DEFAULT_ORDER_DIRECTION,
    DEFAULT_ORDER_SORT,
    ORDER_JOINS,
    ORDER_SORTS,
    create_order,
)
from app.sorting import apply_sort

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


def _plain(markup):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", markup)).strip()


def _names(text, header):
    """Значения столбца по ИМЕНИ заголовка, а не по номеру: номер колонки у
    разных прав разный."""
    head = text[text.index("<thead"):text.index("</thead>")]
    body = text[text.index("<tbody"):text.index("</tbody>")]
    headers = [_plain(c).replace("▼", "").replace("▲", "").strip()
               for c in re.findall(r"<th[^>]*>([\s\S]*?)</th>", head)]
    index = headers.index(header)
    rows = [[_plain(c) for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
            for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", body) if "<td" in row]
    return [row[index] for row in rows]


def _product(name, sku, unit_id, category_id=None):
    return Product(name=name, sku=sku, unit_id=unit_id, category_id=category_id,
                   price=1.0, rest=1)


def test_the_relation_headers_are_offered_as_sortable(client, db_session):
    _admin(client, db_session)
    for url, key in [("/products", "category"), ("/products", "unit"),
                     ("/orders", "customer")]:
        text = client.get(url).text
        assert f"sort={key}&dir=" in text, f"{url}: в шапке нет колонки {key}"


def test_sorting_a_product_by_category_follows_the_related_name(client, db_session):
    """Не по id категории, а по названию: в ячейке имя, и порядок по id выглядел
    бы случайным."""
    _admin(client, db_session)
    unit = _unit_id(db_session)
    nails = Category(name="А крепёж")
    paint = Category(name="Б краска")
    db_session.add_all([nails, paint])
    db_session.flush()
    db_session.add_all([
        _product("Шуруп", "S1", unit, nails.id),
        _product("Краска", "P1", unit, paint.id),
    ])
    db_session.commit()
    assert _names(client.get("/products?sort=category&dir=asc").text,
                  "Название") == ["Шуруп", "Краска"]
    assert _names(client.get("/products?sort=category&dir=desc").text,
                  "Название") == ["Краска", "Шуруп"]


def test_sorting_a_product_by_the_unit_follows_the_related_name(client, db_session):
    _admin(client, db_session)
    meters = get_unit_by_name(db_session, "м")
    pieces = get_unit_by_name(db_session, "шт")
    db_session.add_all([
        _product("Провод", "W1", meters.id),
        _product("Гвоздь", "N1", pieces.id),
    ])
    db_session.commit()
    # «м» идёт раньше «шт» и по-русски, и по байтам: UTF-8 кодирует русский
    # алфавит подряд, так что порядок сходится и на SQLite, и на PostgreSQL.
    assert _names(client.get("/products?sort=unit&dir=asc").text,
                  "Название") == ["Провод", "Гвоздь"]


def test_sorting_orders_by_customer_follows_the_related_name(client, db_session):
    _admin(client, db_session)
    product = _product("Гвозди", "N1", _unit_id(db_session))
    first = Customer(name="А первый", personal_discount=0.0)
    second = Customer(name="Б второй", personal_discount=0.0)
    db_session.add_all([product, first, second])
    db_session.commit()
    for customer in (second, first):
        create_order(db_session, customer_id=customer.id, discount=0,
                     items=[{"product_id": product.id, "quantity": 1, "discount": 0}])
    assert _names(client.get("/orders?sort=customer&dir=asc").text,
                  "Клиент") == ["А первый", "Б второй"]
    assert _names(client.get("/orders?sort=customer&dir=desc").text,
                  "Клиент") == ["Б второй", "А первый"]


def test_a_product_without_a_category_does_not_vanish_when_sorting_by_it(client, db_session):
    """Внутреннее присоединение убрало бы такой товар со страницы целиком — для
    человека это выглядит как «товар потерялся», хотя он на месте."""
    _admin(client, db_session)
    unit = _unit_id(db_session)
    paint = Category(name="Б краска")
    db_session.add(paint)
    db_session.flush()
    db_session.add_all([
        _product("Без категории", "N1", unit),
        _product("Краска", "P1", unit, paint.id),
    ])
    db_session.commit()
    text = client.get("/products?sort=category&dir=asc").text
    assert _names(text, "Название") == ["Без категории", "Краска"], \
        "товар без категории исчез из списка"
    assert "Всего: 2" in text, "и счётчик потерял строку"


def test_a_product_whose_category_is_deleted_does_not_vanish(client, db_session):
    """Категории удаляются мягко, и обычный JOIN их нашёл бы. Проверка ловит
    заранее случай, когда удаление станет жёстким: страна не должна терять товар."""
    _admin(client, db_session)
    unit = _unit_id(db_session)
    gone = Category(name="А исчезнет")
    db_session.add(gone)
    db_session.flush()
    db_session.add(_product("Гвоздь", "N1", unit, gone.id))
    db_session.commit()
    assert client.delete(f"/api/categories/{gone.id}",
                         params={"resolve": "true"}).status_code in (200, 204)
    text = client.get("/products?sort=category&dir=asc").text
    assert _names(text, "Название") == ["Гвоздь"], "товар с удалённой категорией исчез"


def test_an_order_whose_customer_is_deleted_does_not_vanish(client, db_session):
    _admin(client, db_session)
    product = _product("Гвозди", "N1", _unit_id(db_session))
    gone = Customer(name="ООО Ушло", personal_discount=0.0)
    db_session.add_all([product, gone])
    db_session.commit()
    create_order(db_session, customer_id=gone.id, discount=0,
                 items=[{"product_id": product.id, "quantity": 1, "discount": 0}])
    assert client.delete(f"/api/customers/{gone.id}").status_code == 204
    text = client.get("/orders?sort=customer&dir=asc").text
    assert _names(text, "Клиент") == ["ООО Ушло"], "заказ удалённого клиента исчез"


def test_the_relation_headers_carry_the_same_indicator_as_the_own_columns(client, db_session):
    """Иначе человек не поймёт, почему порядок изменился: заголовок выглядит так
    же, как несортируемый, и причина не видна."""
    _admin(client, db_session)
    db_session.add(Customer(name="А"))
    db_session.commit()
    for url, key, header in [("/products", "category", "Категория"),
                             ("/products", "unit", "Ед."),
                             ("/orders", "customer", "Клиент")]:
        text = client.get(url, params={"sort": key, "dir": "desc"}).text
        marked = [l for l in text.splitlines() if "▲" in l or "▼" in l]
        assert len(marked) == 1, f"{url}/{key}: стрелка в {len(marked)} заголовках, а в одном"
        assert header in marked[0], \
            f"{url}/{key}: стрелка не у колонки «{header}»: {marked[0].strip()}"
        assert "▼" in marked[0], f"{url}/{key}: направление не показано"
        assert 'aria-sort="descending"' in marked[0], f"{url}/{key}: нет aria-sort"
        assert "dir=asc" in marked[0], "повторный клик не перевернёт порядок"
        assert "aria-sort" not in client.get(url).text, \
            f"{url}: стрелка стоит без сортировки"


def _joined_tables(sql: str) -> set[str]:
    return {m.lower() for m in re.findall(r"LEFT OUTER JOIN (\w+)", sql.upper())}


def test_a_hostile_key_cannot_reach_the_join_of_a_relation_sort(db_session):
    """Ключ из адресной строки выбирает теперь не только колонку, но и
    присоединение. Присоединение берётся из белого списка, а не из строки, то
    есть чужая строка не может ни подставить таблицу, ни убрать лишнюю.

    Сравнение идёт с запросом по умолчанию, а не с пустым множеством: у товара
    `unit_rel` объявлен с `lazy="joined"`, то есть `LEFT OUTER JOIN units` есть
    в любом запросе, и проверка «JOIN не должно быть» ругалась бы на код,
    который писали не здесь."""
    for model, sorts, default_sort, default_dir, joins in [
        (Product, PRODUCT_SORTS, DEFAULT_PRODUCT_SORT, DEFAULT_PRODUCT_DIRECTION,
         PRODUCT_JOINS),
        (Order, ORDER_SORTS, DEFAULT_ORDER_SORT, DEFAULT_ORDER_DIRECTION, ORDER_JOINS),
    ]:
        plain = apply_sort(db_session.query(model), model, sorts, default_sort,
                           default_dir, None, None, joins)
        plain_sql = str(plain.statement.compile(db_session.get_bind(),
                                                compile_kwargs={"literal_binds": True}))
        for hostile in ("category) ; DROP TABLE products--", "unit", "customers",
                        "id; SELECT 1"):
            query = apply_sort(db_session.query(model), model, sorts, default_sort,
                               default_dir, hostile, "asc", joins)
            sql = str(query.statement.compile(db_session.get_bind(),
                                              compile_kwargs={"literal_binds": True}))
            assert "DROP" not in sql.upper() and "SELECT 1" not in sql, \
                "чужая строка дошла до SQL: " + sql.split("ORDER BY")[1]
            assert _joined_tables(sql) == _joined_tables(plain_sql), \
                (f"ключ {hostile!r} изменил набор присоединений: "
                 f"{sorted(_joined_tables(sql))} против {sorted(_joined_tables(plain_sql))}")
        assert "DROP" not in plain_sql.upper()


def test_the_join_is_added_only_for_the_chosen_key(db_session):
    """Присоединение на каждый запрос — лишняя работа и лишний риск: у товара уже
    есть своё присоединение единицы, и лишнее левое джойн меняет её нумерацию."""
    for model, sorts, default_sort, default_dir, joins, key in [
        (Product, PRODUCT_SORTS, DEFAULT_PRODUCT_SORT, DEFAULT_PRODUCT_DIRECTION,
         PRODUCT_JOINS, "category"),
        (Order, ORDER_SORTS, DEFAULT_ORDER_SORT, DEFAULT_ORDER_DIRECTION,
         ORDER_JOINS, "customer"),
    ]:
        query = apply_sort(db_session.query(model), model, sorts, default_sort,
                           default_dir, key, "asc", joins)
        sql = str(query.statement.compile(db_session.get_bind(),
                                          compile_kwargs={"literal_binds": True}))
        assert "LEFT OUTER JOIN" in sql, f"{key}: соединение не внешнее: {sql[:200]}"
        plain = apply_sort(db_session.query(model), model, sorts, default_sort,
                           default_dir, None, None, joins)
        plain_sql = str(plain.statement.compile(db_session.get_bind(),
                                                compile_kwargs={"literal_binds": True}))
        assert "LEFT OUTER JOIN categories" not in plain_sql, \
            "сортировка по умолчанию тащит присоединение категории"
        assert "LEFT OUTER JOIN customers" not in plain_sql, \
            "сортировка по умолчанию тащит присоединение клиента"


def test_the_default_order_is_still_the_one_without_any_join(db_session):
    _admin_query = apply_sort(db_session.query(Order), Order, ORDER_SORTS,
                              DEFAULT_ORDER_SORT, DEFAULT_ORDER_DIRECTION, None, None,
                              ORDER_JOINS)
    sql = str(_admin_query.statement.compile(db_session.get_bind(),
                                             compile_kwargs={"literal_binds": True}))
    assert "ORDER BY orders.number DESC, orders.id DESC" in sql, sql.split("ORDER BY")[1]


def test_paging_by_a_relation_column_neither_repeats_nor_drops_a_row(db_session):
    """Второй ключ `id` обязателен и здесь: у шести товаров одна категория, и без
    второго ключа `OFFSET` делит совпадающие строки как бог на душу.

    Проверяется через сервис, а не через страницу: `PAGE_SIZE` на странице 25, и
    шесть товаров на три страницы не разложить — пришлось бы заводить 51 товар
    ради проверки постраничности."""
    unit = _unit_id(db_session)
    shared = Category(name="А общее")
    db_session.add(shared)
    db_session.flush()
    for i in range(6):
        db_session.add(_product(f"Товар {i}", f"T{i}", unit, shared.id))
    db_session.commit()
    seen = []
    for page in (1, 2, 3):
        items, total = list_products(db_session, page=page, page_size=2,
                                     sort="category", direction="asc")
        assert total == 6
        seen.extend(p.id for p in items)
    assert len(seen) == len(set(seen)) == 6, f"страницы дали {seen}"


def test_the_allowlist_and_the_header_still_agree(client, db_session):
    """Расхождение между белым списком и шапкой — это либо 500 по клику, либо
    колонка в шапке, которая ничего не делает."""
    _admin(client, db_session)
    for url, sorts in [("/products", PRODUCT_SORTS), ("/orders", ORDER_SORTS)]:
        offered = {key for key, _ in re.findall(
            rf'href="{re.escape(url)}\?[^"]*sort=(\w+)&dir=(\w+)"',
            client.get(url).text)}
        assert offered == set(sorts), \
            f"{url}: в шапке {sorted(offered)}, в списке {sorted(sorts)}"


def test_each_joined_key_names_a_column_of_the_table_it_joins():
    for joins in (PRODUCT_JOINS, ORDER_JOINS):
        for key, (target, onclause) in joins.items():
            assert onclause is not None, f"{key}: присоединение без условия"
            table = target.__table__
            assert any(c.name == "id" for c in table.columns), f"{key}: у цели нет id"
