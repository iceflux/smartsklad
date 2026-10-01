import re

from app.formatting import GROUP_SEPARATOR, group_number
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name
from app.modules.orders.service import create_order

NBSP = "\u00a0"
PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)


def _order(db_session, price, quantity=1, discount=0, sku="NAIL-1"):
    product = Product(name="Гвозди", sku=sku, unit_id=get_unit_by_name(db_session, "\u0448\u0442").id,
                      price=price, rest=100000)
    customer = Customer(name="\u041e\u041e\u041e \u0421\u0442\u0440\u043e\u0439", inn="7701234567")
    db_session.add_all([product, customer])
    db_session.commit()
    order = create_order(db_session, customer_id=customer.id, discount=discount,
                         items=[{"product_id": product.id, "quantity": quantity, "discount": 0}])
    return product, customer, order


def test_the_separator_is_a_non_breaking_space_and_not_an_ordinary_one():
    assert GROUP_SEPARATOR == NBSP
    assert GROUP_SEPARATOR != " "
    grouped = group_number(1000000)
    assert NBSP in grouped
    assert " " not in grouped


def test_a_round_million_is_grouped():
    assert group_number(1000000) == f"1{NBSP}000{NBSP}000"


def test_a_value_with_decimals_keeps_two_of_them():
    assert group_number(1234567.891, 2) == f"1{NBSP}234{NBSP}567.89"
    assert group_number(1234567.891, 2).endswith(".89")


def test_the_minus_stands_before_the_grouped_digits():
    assert group_number(-1234567) == f"-1{NBSP}234{NBSP}567"


def test_zero_and_small_numbers_keep_their_shape():
    assert group_number(0) == "0"
    assert group_number(999) == "999"
    assert group_number(1000) == f"1{NBSP}000"


def test_a_zero_padded_identifier_keeps_its_padding():
    assert group_number("00001") == f"00{NBSP}001"


def test_none_and_text_do_not_raise_and_do_not_get_grouped():
    assert group_number(None) == "\u2014"
    assert group_number("1C-00001") == "1C-00001"
    assert group_number("\u041d\u0435\u0442") == "\u041d\u0435\u0442"
    assert group_number("") == ""
    assert group_number(1000000) == group_number("1000000")


def test_the_money_on_the_order_page_is_grouped(client, db_session):
    _admin(client, db_session)
    _product, _customer, order = _order(db_session, 1234567.89)
    body = client.get(f"/orders/{order.id}").text
    assert f"1{NBSP}234{NBSP}567.89" in body, "сумма заказа не сгруппирована"


def test_the_money_on_the_order_list_and_the_print_document_is_grouped(client, db_session):
    _admin(client, db_session)
    _order(db_session, 1234567.89)
    listed = client.get("/orders").text
    assert f"1{NBSP}234{NBSP}567.89" in listed
    assert "1234567.89" not in listed
    printed = client.get("/orders/1/print").text
    assert f"1{NBSP}234{NBSP}567.89" in printed
    assert "1234567.89" not in printed, "в печатном документе число не сгруппировано"


def test_the_money_on_the_dashboard_is_grouped(client, db_session):
    _admin(client, db_session)
    _product, _customer, order = _order(db_session, 1234567.89)
    order.status = "done"
    db_session.commit()
    body = client.get("/dashboard").text
    assert f"1{NBSP}234{NBSP}567.89" in body
    assert "1234567.89" not in body


def test_a_large_stock_figure_is_grouped(client, db_session):
    _admin(client, db_session)
    _order(db_session, 1234567.89)
    body = client.get("/products").text
    assert re.search(rf"100{NBSP}000", body), "остаток не сгруппирован"


def test_an_inn_and_an_article_are_left_alone(client, db_session):
    _admin(client, db_session)
    _order(db_session, 1234567.89, sku="12345678")
    customer_body = client.get("/customers/1").text
    assert "7701234567" in customer_body, "ИНН должен печататься как есть"
    assert f"77{NBSP}01" not in customer_body
    products_body = client.get("/products").text
    assert "12345678" in products_body, "артикул должен печататься как есть"
    assert f"12{NBSP}345{NBSP}678" not in products_body


def test_an_order_number_used_as_an_identifier_stays_ungrouped(client, db_session):
    _admin(client, db_session)
    _product, _customer, order = _order(db_session, 1234567.89)
    body = client.get("/orders").text
    number = re.search(r"href=\"/orders/\d+\"[^>]*>([^<]+)</a>", body).group(1)
    assert number == order.number, (number, order.number)
    assert NBSP not in number
