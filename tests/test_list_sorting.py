"""Сортировка списка по клику на заголовок колонки.

Ключ сортировки приходит из адресной строки, то есть от человека, поэтому он
проверяется на границе: `app/sorting.py` ищет его в белом списке колонок раздела
и подставляет объект колонки, а не строку. Неизвестный ключ не отвергается, а
заменяется ключом по умолчанию — иначе опечатка в ссылке отдавала бы 500.

Второй ключ (`id`) проверяется по собранному SQL, а не «по списку»:
забытый второй ключ не падает, он молча ломает постраничный разбор — страница
отдаёт одну строку дважды, а другую не отдаёт вовсе.
"""

import re

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.customers.service import (CUSTOMER_SORTS, DEFAULT_CUSTOMER_DIRECTION,
                                           DEFAULT_CUSTOMER_SORT, list_customers)
from app.modules.inventory.models import Product
from app.modules.inventory.service import (DEFAULT_PRODUCT_DIRECTION, DEFAULT_PRODUCT_SORT,
                                           PRODUCT_SORTS, get_unit_by_name, list_products)
from app.modules.orders.service import (DEFAULT_ORDER_DIRECTION, DEFAULT_ORDER_SORT,
                                        ORDER_SORTS, list_orders)
from app.sorting import apply_sort

PASSWORD = "password123"
PAGES = {"/customers": 4, "/products": 4, "/orders": 4}


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


def _table(text):
    head = text[text.index("<thead"):text.index("</thead>")]
    body = text[text.index("<tbody"):text.index("</tbody>")]
    headers = [_plain(c).replace("▼", "").replace("▲", "").strip()
               for c in re.findall(r"<th[^>]*>([\s\S]*?)</th>", head)]
    rows = [[_plain(c) for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
            for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", body) if "<td" in row]
    return headers, rows


def _names(text, header):
    """Значения одного столбца по ИМЕНИ заголовка, а не по номеру: у товара
    первым идёт столбец с галочками, и он есть только у того, кто может
    редактировать, — номер колонки у разных прав разный."""
    headers, rows = _table(text)
    index = headers.index(header)
    return [row[index] for row in rows]


def test_every_section_page_offers_four_sortable_headers(client, db_session):
    _admin(client, db_session)
    for url, expected in PAGES.items():
        links = re.findall(rf'href="{re.escape(url)}\?[^"]*sort=(\w+)&dir=(\w+)"',
                           client.get(url).text)
        assert len(links) == expected, f"{url}: сортируемых колонок {len(links)}, а ждём {expected}"
        assert len({key for key, _ in links}) == expected, f"{url}: повторяющиеся колонки"


def test_the_columns_that_are_not_columns_are_not_sorted(client, db_session):
    """Категория, Ед. и Действия у товара и Клиент у заказа нарисованы не из
    своей колонки: там имя из связи. Сортировка по ним потребовала бы JOIN, а
    обещание «кликните и увидите» дешевле оставить пустым, чем сделать
    наполовину."""
    _admin(client, db_session)
    for url, header in [("/products", "Категория"), ("/products", "Ед."),
                        ("/products", "Действия"), ("/customers", "Действия"),
                        ("/orders", "Клиент")]:
        text = client.get(url).text
        line = next(l for l in text.splitlines() if f">{header}<" in l)
        assert "<a " not in line, f"{url}: колонка «{header}» unexpectedly sortable"


def test_the_list_follows_the_chosen_column_and_direction(client, db_session):
    _admin(client, db_session)
    for name in ["Б", "А", "В"]:
        db_session.add(Customer(name=name))
    db_session.commit()
    asc = _names(client.get("/customers?sort=name&dir=asc").text, "Имя")
    desc = _names(client.get("/customers?sort=name&dir=desc").text, "Имя")
    assert asc == sorted(asc) and desc == sorted(desc, reverse=True)
    assert set(asc) == set(desc) == {"А", "Б", "В"}


def test_money_and_stock_sort_numerically_not_as_text(client, db_session):
    _admin(client, db_session)
    for name, price, rest in [("дешёвая", 9.0, 100), ("дорогая", 100.0, 9)]:
        db_session.add(Product(name=name, sku=f"S-{name}", unit_id=_unit_id(db_session),
                               price=price, rest=rest))
    db_session.commit()
    assert _names(client.get("/products?sort=price&dir=asc").text, "Название") == ["дешёвая", "дорогая"]
    assert _names(client.get("/products?sort=rest&dir=asc").text, "Остаток") == ["9", "100"]


def test_the_default_order_of_each_page_is_the_one_it_had_before(client, db_session):
    """Загрузка страницы без сортировки обязана выглядеть как раньше: клиенты и
    товары по имени, заказы новые сверху."""
    _admin(client, db_session)
    db_session.add_all([Customer(name="Б"), Customer(name="А")])
    db_session.commit()
    assert _names(client.get("/customers").text, "Имя") == ["А", "Б"]
    db_session.add_all([Product(name="Б", sku="S1", unit_id=_unit_id(db_session), rest=50),
                        Product(name="А", sku="S2", unit_id=_unit_id(db_session), rest=50)])
    db_session.commit()
    assert _names(client.get("/products").text, "Название") == ["А", "Б"]
    from app.modules.orders.service import create_order
    customer = db_session.query(Customer).order_by(Customer.id).first()
    first = create_order(db_session, customer_id=customer.id, discount=0,
                         items=[{"product_id": db_session.query(Product).order_by(Product.id).first().id,
                                 "quantity": 1, "discount": 0}])
    second = create_order(db_session, customer_id=customer.id, discount=0,
                          items=[{"product_id": db_session.query(Product).order_by(Product.id).first().id,
                                  "quantity": 2, "discount": 0}])
    assert _names(client.get("/orders").text, "Номер") == [second.number, first.number]
    assert (DEFAULT_CUSTOMER_SORT, DEFAULT_CUSTOMER_DIRECTION) == ("name", "asc")
    assert (DEFAULT_PRODUCT_SORT, DEFAULT_PRODUCT_DIRECTION) == ("name", "asc")
    assert (DEFAULT_ORDER_SORT, DEFAULT_ORDER_DIRECTION) == ("number", "desc")


def test_the_sorted_column_shows_a_visible_indicator_and_nothing_else_does(client, db_session):
    _admin(client, db_session)
    db_session.add(Customer(name="А"))
    db_session.commit()
    text = client.get("/customers?sort=name&dir=desc").text
    marked = [l for l in text.splitlines() if "▲" in l or "▼" in l]
    assert len(marked) == 1, f"стрелка стоит в {len(marked)} заголовках, а должна в одном"
    assert "▼" in marked[0]
    assert 'aria-sort="descending"' in marked[0]
    assert "dir=asc" in marked[0], "повторный клик по той же колонке обязан перевернуть порядок"
    assert "▲" in client.get("/customers?sort=name&dir=asc").text
    assert "aria-sort" not in client.get("/customers").text


def test_the_sort_is_a_link_and_survives_paging_and_the_live_filter(client, db_session):
    _admin(client, db_session)
    for name in [f"Клиент {i:03d}" for i in range(60)]:
        db_session.add(Customer(name=name))
    db_session.commit()
    text = client.get("/customers?sort=name&dir=desc").text
    forward = re.search(r'href="(/customers\?[^"]*page=2)"', text)
    assert forward, "на странице с результатами нет перехода вперёд"
    assert "sort=name" in forward.group(1) and "dir=desc" in forward.group(1), \
        "сортировка потерялась при переходе на следующую страницу"
    form = re.search(r"<form[^>]*data-live-filter[^>]*>([\s\S]*?)</form>", text).group(1)
    assert '<input type="hidden" name="sort" value="name">' in form, \
        "живая фильтрация сбросит сортировку, если не передать её формой"
    assert '<input type="hidden" name="dir" value="desc">' in form


def test_the_reset_link_returns_to_the_default_order(client, db_session):
    _admin(client, db_session)
    assert 'href="/customers" class="app-link' in client.get("/customers?sort=name&dir=desc").text


def test_the_sort_key_is_taken_from_an_allowlist_not_from_the_query_string(client, db_session):
    _admin(client, db_session)
    db_session.add_all([Customer(name="А"), Customer(name="Б")])
    db_session.commit()
    hostile = "name); DROP TABLE customers--"
    r = client.get("/customers", params={"sort": hostile, "dir": "asc"})
    assert r.status_code == 200
    assert _names(r.text, "Имя") == ["А", "Б"], "чужой ключ не должен влиять на порядок"
    assert db_session.query(Customer).count() == 2, "таблица должна быть на месте"


def test_the_hostile_key_never_reaches_the_sql(client, db_session):
    _admin(client, db_session)
    query = apply_sort(db_session.query(Customer), Customer, CUSTOMER_SORTS,
                       DEFAULT_CUSTOMER_SORT, DEFAULT_CUSTOMER_DIRECTION,
                       "name); DROP TABLE customers--", "asc")
    sql = str(query.statement.compile(db_session.get_bind(),
                                      compile_kwargs={"literal_binds": True}))
    assert "DROP" not in sql.upper()
    assert "ORDER BY customers.name ASC, customers.id ASC" in sql


def test_an_unknown_direction_falls_back_instead_of_reaching_the_query(client, db_session):
    """Направление тоже приходит от человека, поэтому оно не строка в SQL:
    `_direction` умеет только `.asc()` и `.desc()`, то есть выбрать можно
    ровно два порядка, а любое другое слово означает «порядок по умолчанию»."""
    _admin(client, db_session)
    db_session.add_all([Customer(name="А"), Customer(name="Б")])
    db_session.commit()
    r = client.get("/customers", params={"sort": "name", "dir": "asc; --"})
    assert r.status_code == 200
    assert _names(r.text, "Имя") == ["А", "Б"]
    query = apply_sort(db_session.query(Customer), Customer, CUSTOMER_SORTS,
                       DEFAULT_CUSTOMER_SORT, "desc", "name", "asc; DROP TABLE customers--")
    sql = str(query.statement.compile(db_session.get_bind(),
                                      compile_kwargs={"literal_binds": True}))
    assert "DROP" not in sql.upper()
    assert sql.split("ORDER BY")[1].strip() == "customers.name DESC, customers.id DESC", sql


def test_every_sortable_query_carries_the_stable_second_key(db_session):
    for model, sorts, default_sort, default_dir, key in [
        (Customer, CUSTOMER_SORTS, DEFAULT_CUSTOMER_SORT, DEFAULT_CUSTOMER_DIRECTION, "inn"),
        (Product, PRODUCT_SORTS, DEFAULT_PRODUCT_SORT, DEFAULT_PRODUCT_DIRECTION, "price"),
    ]:
        for direction in ("asc", "desc"):
            query = apply_sort(db_session.query(model), model, sorts, default_sort,
                               default_dir, key, direction)
            sql = str(query.statement.compile(db_session.get_bind(),
                                              compile_kwargs={"literal_binds": True}))
            order = sql.split("ORDER BY")[1]
            assert order.count(",") == 1, f"{model.__tablename__}: в ORDER BY один ключ: {order}"
            assert order.rstrip().split(",")[-1].strip() == f"{model.__tablename__}.id {direction.upper()}", \
                f"{model.__tablename__}: второго ключа нет, постраничный разбор будет рвать строки"


def test_paging_over_equal_keys_neither_repeats_nor_drops_a_row(db_session):
    """Шесть клиентов с одинаковым именем — случай, ради которого нужен второй
    ключ: без него `OFFSET` делит совпадающие строки как бог на душу."""
    for _ in range(6):
        db_session.add(Customer(name="Одинаковые", personal_discount=0.0))
    db_session.commit()
    seen = []
    for page in (1, 2, 3):
        items, total = list_customers(db_session, page=page, page_size=2)
        assert total == 6
        seen.extend(c.id for c in items)
    assert len(seen) == len(set(seen)) == 6, f"страницы дали {seen}"


def test_the_order_list_and_the_product_list_take_the_sort_too(client, db_session):
    _admin(client, db_session)
    from app.modules.orders.service import create_order
    product = Product(name="Гвозди", sku="N1", unit_id=_unit_id(db_session), price=1.0, rest=100)
    customer = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add_all([product, customer])
    db_session.commit()
    for qty in (1, 5, 3):
        create_order(db_session, customer_id=customer.id, discount=0,
                     items=[{"product_id": product.id, "quantity": qty, "discount": 0}])
    text = client.get("/orders?sort=amount&dir=asc").text
    amounts = [float(s.replace("\u00a0", "").replace(" ", "")) for s in _names(text, "Сумма")]
    assert amounts == sorted(amounts), amounts


def test_the_allowlist_covers_exactly_the_headers_the_page_offers(client, db_session):
    """Расхождение между белым списком в сервисе и ссылками в шаблоне — это
    либо 500 по клику, либо колонка в шапке, которая ничего не делает."""
    _admin(client, db_session)
    for url, sorts in [("/customers", CUSTOMER_SORTS), ("/products", PRODUCT_SORTS),
                       ("/orders", ORDER_SORTS)]:
        offered = {key for key, _ in re.findall(
            rf'href="{re.escape(url)}\?[^"]*sort=(\w+)&dir=(\w+)"', client.get(url).text)}
        assert offered == set(sorts), f"{url}: в шапке {sorted(offered)}, в списке {sorted(sorts)}"


def test_the_sortable_columns_are_real_columns_of_their_table():
    """Ключ в белом списке обязан быть именем колонки своей таблицы. Опечатка
    здесь стоила бы 500 на любом клике по заголовку — и только на этом клике."""
    from app.modules.orders.models import Order
    for model, sorts in [(Customer, CUSTOMER_SORTS), (Product, PRODUCT_SORTS),
                         (Order, ORDER_SORTS)]:
        for key, column in sorts.items():
            assert column.key in model.__table__.columns, \
                f"{model.__tablename__}: {column.key} не колонка — опечатка в белом списке"


def _search_hits(text):
    body = text[text.index("<h2 class=\"app-group-title\">Клиенты"):text.index("</section>")]
    return [_plain(h) for h in re.findall(r'class="app-link[^"]*"[^>]*>([^<]+)<', body)]


def test_the_header_search_keeps_its_own_order(client, db_session):
    """Поиск в шапке сортировку страницы не принимает: он ищет сразу по трём
    разделам, и порядок там задаёт страница поиска, а не выбранная колонка
    товара. Это решение, а не недосмотр: `sort` на `/search` просто не читается."""
    _admin(client, db_session)
    for name in ["Ромашка Б", "Ромашка А"]:
        db_session.add(Customer(name=name))
    db_session.commit()
    found = client.get("/search", params={"q": "Ромашка", "sort": "name", "dir": "desc"}).text
    assert _search_hits(found) == ["Ромашка А", "Ромашка Б"]
