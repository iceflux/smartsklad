import pytest

from app.modules.audit.models import AuditLog
from app.modules.customers.models import Customer
from app.modules.customers.service import (
    create_customer, delete_customer, get_customer, list_customers,
    restore_customer, update_customer,
)


def _make_user(db_session):
    from app.modules.auth.models import User
    from app.modules.auth.service import get_role_by_name, hash_password
    u = User(username="c1", password_hash=hash_password("password123"),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def user(db_session):
    return _make_user(db_session)


def test_create_customer_ok(db_session):
    c = create_customer(db_session, name="ООО Строй", inn="7701234567",
                        phone="+79990001122", email="a@b.ru",
                        discount_card="C-001", personal_discount=5.0,
                        comment="постоянный", user=None)
    assert c.id is not None
    assert c.name == "ООО Строй"
    assert c.personal_discount == 5.0
    assert c.is_deleted is False
    log = db_session.query(AuditLog).filter_by(action="customer.create").first()
    assert log is not None


@pytest.mark.parametrize("name", ["", "   "])
def test_create_customer_empty_name(db_session, name):
    with pytest.raises(ValueError):
        create_customer(db_session, name=name)


@pytest.mark.parametrize("inn,ok", [
    ("7701234567", True),      # 10
    ("770123456789", True),    # 12
    ("770123456", False),      # 9
    ("7701234567890", False),  # 13
    ("770123456a", False),     # не цифры
    (None, True),              # не задан — ок
    ("", True),                # пустая строка = не задан — ок (мягкая валидация)
])
def test_create_customer_inn_validation(db_session, inn, ok):
    if ok:
        create_customer(db_session, name="X", inn=inn)
    else:
        with pytest.raises(ValueError):
            create_customer(db_session, name="X", inn=inn)


@pytest.mark.parametrize("d", [-1, 100, 150])
def test_create_customer_discount_out_of_range(db_session, d):
    with pytest.raises(ValueError):
        create_customer(db_session, name="X", personal_discount=d)


def test_update_customer_fields_and_audit(db_session, user):
    c = create_customer(db_session, name="A", inn="7701234567", user=user)
    updated = update_customer(db_session, c.id, comment="новый", personal_discount=10.0, user=user)
    assert updated.comment == "новый"
    assert updated.personal_discount == 10.0
    log = db_session.query(AuditLog).filter_by(action="customer.update").order_by(AuditLog.id.desc()).first()
    assert log is not None
    assert "personal_discount" in log.new_value or "comment" in log.new_value


def test_update_customer_missing_raises(db_session, user):
    with pytest.raises(ValueError):
        update_customer(db_session, 9999, user=user)


def test_delete_and_restore(db_session, user):
    c = create_customer(db_session, name="A", user=user)
    delete_customer(db_session, c.id, user=user)
    assert db_session.get(Customer, c.id).is_deleted is True
    restore_customer(db_session, c.id, user=user)
    assert db_session.get(Customer, c.id).is_deleted is False


def test_list_customers_search_pagination(db_session):
    create_customer(db_session, name="ООО Строй", phone="+79990001122", inn="7701234567")
    create_customer(db_session, name="Иван", phone="+79991112233", inn="333333333333")
    all_items, total = list_customers(db_session, search="строй")
    assert total == 1
    assert all_items[0].name == "ООО Строй"
    by_phone, t = list_customers(db_session, search="911122")
    assert t == 1 and by_phone[0].name == "Иван"
    by_inn, t2 = list_customers(db_session, search="7701234567")
    assert t2 == 1 and by_inn[0].name == "ООО Строй"
    every, total_all = list_customers(db_session)
    assert total_all == 2


def test_list_customers_include_deleted(db_session, user):
    c = create_customer(db_session, name="A", user=user)
    delete_customer(db_session, c.id, user=user)
    alive, t = list_customers(db_session)
    assert t == 0
    allx, t2 = list_customers(db_session, include_deleted=True)
    assert t2 == 1 and allx[0].is_deleted is True