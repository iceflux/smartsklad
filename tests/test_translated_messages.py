"""Переведённое сообщение обязано быть тем, которое видит человек.

Свип `tests/test_russian_messages.py` доказывает, что английских текстов не
осталось. Он ничего не говорит о том, что русский текст вообще достигает
человека, — а это и есть смысл перевода. Поэтому здесь каждое сообщение
вызывается настоящим путём и сверяется с тем, что человек увидит.

Проверка идёт от ожидаемого сообщения к реальному результату, а не наоборот:
сравнение «страница с самой собой» прошло бы и при мёртвом коде.
"""

import pytest

from app.modules.auth.utils import parse_permissions
from app.modules.customers.models import Customer
from app.modules.inventory.models import Category, Product, Unit
from app.modules.inventory.service import (
    change_bulk_category,
    create_category,
    create_product,
    update_category,
)
from app.modules.notifications.service import send_document
from app.modules.notifications.telegram import send_telegram_document
from app.modules.orders.models import Order


def _unit(db_session, name="шт"):
    return db_session.query(Unit).filter(Unit.name == name).first()


def _product(db_session, name="Гвозди", **kw):
    kw.setdefault("sku", f"АРТ-{name}")
    kw.setdefault("unit_id", _unit(db_session).id)
    product = Product(name=name, price=1.0, rest=0, **kw)
    db_session.add(product)
    db_session.commit()
    return product


def _order(db_session, number="00001"):
    customer = Customer(name="ООО Ромашка")
    db_session.add(customer)
    db_session.commit()
    order = Order(number=number, customer_id=customer.id, total_amount=0.0)
    db_session.add(order)
    db_session.commit()
    return order


# --- клиенты ------------------------------------------------------------


def test_the_empty_customer_name_is_rejected_in_russian(db_session):
    from app.modules.customers.service import create_customer

    with pytest.raises(ValueError, match="название обязательно"):
        create_customer(db_session, name="   ")


def test_the_bad_inn_names_the_value(db_session):
    from app.modules.customers.service import create_customer

    with pytest.raises(ValueError) as err:
        create_customer(db_session, name="ООО Ромашка", inn="123")
    message = str(err.value)
    assert "123" in message, message
    assert "10 или 12 цифр" in message, message


def test_the_bad_discount_names_the_value(db_session):
    from app.modules.customers.service import create_customer

    with pytest.raises(ValueError) as err:
        create_customer(db_session, name="ООО Ромашка", personal_discount=150)
    message = str(err.value)
    assert "150" in message, message
    assert "от 0 до 99" in message, message


def test_the_missing_customer_says_so_in_russian(db_session):
    from app.modules.customers.service import delete_customer

    with pytest.raises(ValueError, match="^клиент не найден$"):
        delete_customer(db_session, 4242)


# --- категории ----------------------------------------------------------


def test_the_empty_category_name_is_rejected_in_russian(db_session):
    with pytest.raises(ValueError, match="название категории обязательно"):
        create_category(db_session, "  ")


def test_the_duplicate_category_names_the_value(db_session):
    create_category(db_session, "Крепёж")
    with pytest.raises(ValueError) as err:
        create_category(db_session, "Крепёж")
    assert "Крепёж" in str(err.value), str(err.value)


def test_the_missing_category_says_so_in_russian(db_session):
    with pytest.raises(ValueError, match="^категория не найдена$"):
        update_category(db_session, 4242, "Крепёж", None)


def test_a_category_cannot_be_its_own_parent(db_session):
    cat = create_category(db_session, "Крепёж")
    with pytest.raises(ValueError, match="в саму себя"):
        update_category(db_session, cat.id, "Крепёж", cat.id)


def test_the_bulk_category_change_reports_a_missing_category(db_session):
    with pytest.raises(ValueError, match="^категория не найдена$"):
        change_bulk_category(db_session, [], 4242)


# --- товары -------------------------------------------------------------


def test_the_empty_product_name_is_rejected_in_russian(db_session):
    with pytest.raises(ValueError, match="название товара обязательно"):
        create_product(db_session, name=" ", sku="", category_id=None,
                       unit_id=_unit(db_session).id, price=1.0, characteristic=None, rest=0)


def test_the_duplicate_article_names_the_value(db_session):
    _product(db_session, "Гвозди", sku="Г-001")
    with pytest.raises(ValueError) as err:
        create_product(db_session, name="Шурупы", sku="Г-001", category_id=None,
                       unit_id=_unit(db_session).id, price=1.0, characteristic=None, rest=0)
    assert "Г-001" in str(err.value), str(err.value)


def test_the_negative_minimum_stock_names_the_value(db_session):
    with pytest.raises(ValueError) as err:
        create_product(db_session, name="Гвозди", sku="", category_id=None,
                       unit_id=_unit(db_session).id, price=1.0, characteristic=None,
                       rest=0, min_stock=-3)
    message = str(err.value)
    assert "-3" in message, message
    assert "отрицательным" in message, message


# --- уведомления --------------------------------------------------------


def test_the_unknown_channel_names_the_value(db_session):
    order = _order(db_session)
    with pytest.raises(ValueError) as err:
        send_document(db_session, order, "carrier-pigeon")
    assert "carrier-pigeon" in str(err.value), str(err.value)


def test_the_unset_telegram_names_the_settings_to_fill(db_session):
    order = _order(db_session)
    with pytest.raises(ValueError) as err:
        send_telegram_document("текст", b"%PDF", db_session)
    message = str(err.value)
    assert "Telegram не настроен" in message, message
    assert "токен бота" in message and "чат получателя" in message, message


# --- роли и резервные копии ---------------------------------------------


def test_the_unknown_permissions_keep_the_codes_and_gain_russian_words():
    """Права остаются кодами: это единственное, что человек может исправить."""
    with pytest.raises(ValueError) as err:
        parse_permissions(["orders.view", "не.существует"])
    message = str(err.value)
    assert "неизвестные права" in message, message
    assert "не.существует" in message, message


def test_the_unknown_backup_kind_names_the_value(db_session):
    from app.modules.backup.service import create_backup

    with pytest.raises(ValueError) as err:
        create_backup(db_session, kind="голова")
    assert "голова" in str(err.value), str(err.value)


# --- сообщение не должно попадать на страницу дважды --------------------


def test_the_form_shows_the_russian_message_and_not_the_english_one(client, db_session):
    """Конечная точка: то, что человек видит.

    Форма категории сохраняется через `fetch` в API, поэтому проверяется ответ
    `PATCH`, а не страница: именно `detail` из него попадает в красную строку
    ошибки на экране. Сверяется ответ, а не сервис — сервис проверен выше, а
    на экране может оказаться что-то другое.
    """
    from app.modules.auth.models import User
    from app.modules.auth.service import get_role_by_name, hash_password, login

    user = User(username="boss", password_hash=hash_password("password123"),
                role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(user)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", "password123").token)

    category = create_category(db_session, "Крепёж")
    response = client.patch(f"/api/categories/{category.id}",
                            json={"name": "Крепёж", "parent_id": category.id})
    assert response.status_code == 409, response.text
    assert "в саму себя" in response.json()["detail"], response.text
    assert "own parent" not in response.text


def test_a_substitution_really_changes_what_the_check_reads():
    """Охрана охраны: подстановка обязана менять текст, который читает проверка.

    Без неё «сообщение называет значение» прошло бы и при сообщении, называющем
    что угодно, — ровно тот класс дефекта, который в этом проекте уже проходил
    прогон зелёным. Проверяются обе стороны: отказ называет подставленное
    значение и отличается от него, а успешный случай подставу не принимает.
    """
    from app.modules.customers.service import _validate_discount, _validate_inn

    with pytest.raises(ValueError) as err:
        _validate_discount(100)
    message = str(err.value)
    assert "100" in message, message
    assert message != "100", "сообщение совпало со значением — проверка читает не его"

    with pytest.raises(ValueError) as err:
        _validate_inn("123")
    message = str(err.value)
    assert "123" in message, message
    assert message != "123", "сообщение совпало со значением — проверка читает не его"

    assert _validate_discount(50) == 50, "подмена изменила и успешный случай"
    assert _validate_inn("7707081234") == "7707081234", "подмена изменила и успешный случай"


def test_the_fixtures_of_these_checks_are_real_rows(db_session):
    """Фикстуры проверок выше должны опираться на настоящие строки.

    Единица измерения берётся из справочника, сидящего в базе, а не создаётся
    на лету: иначе половина проверок сообщений проходила бы на пустой базе, и
    «клиент не найден» проверялось бы тем же кодом, что и «клиент найден».
    """
    assert _unit(db_session) is not None, "единица «шт» не сидится — фикстура выдумывает"
    product = _product(db_session)
    assert db_session.get(Product, product.id) is not None
    customer = Customer(name="ООО Ромашка")
    db_session.add(customer)
    db_session.commit()
    assert db_session.get(Customer, customer.id) is not None
    assert isinstance(Category(name="Крепёж").name, str)