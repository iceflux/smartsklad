import pytest

from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.service import (
    create_order, change_status,
)
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


@pytest.fixture
def no_rest_product(db_session):
    p = Product(name="Виртуальная", sku="VIRT-1", unit_id=_unit_id(db_session), price=100.0, rest=0, track_rest=False)
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def rest_product(db_session):
    p = Product(name="Физическая", sku="PHYS-1", unit_id=_unit_id(db_session), price=100.0, rest=1, track_rest=True)
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def customer(db_session):
    c = Customer(name="ООО Тест", personal_discount=0.0)
    db_session.add(c)
    db_session.commit()
    return c


def test_no_rest_check_for_untracked(db_session, no_rest_product, customer):
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": no_rest_product.id, "quantity": 5, "discount": 0.0}])
    assert order.status == "new"


def test_create_still_checks_tracked(db_session, rest_product, customer):
    with pytest.raises(ValueError):
        create_order(db_session, customer_id=customer.id,
                     items=[{"product_id": rest_product.id, "quantity": 5, "discount": 0.0}])


def test_done_no_writeoff_for_untracked(db_session, no_rest_product, customer):
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": no_rest_product.id, "quantity": 5, "discount": 0.0}])
    change_status(db_session, order.id, "processing")
    change_status(db_session, order.id, "done")
    p = db_session.get(Product, no_rest_product.id)
    assert p.rest == 0  # не списали


def test_done_writeoff_for_tracked(db_session, rest_product, customer):
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": rest_product.id, "quantity": 1, "discount": 0.0}])
    change_status(db_session, order.id, "processing")
    change_status(db_session, order.id, "done")
    p = db_session.get(Product, rest_product.id)
    assert p.rest == 0

def test_return_noop_for_untracked(db_session, no_rest_product, customer):
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": no_rest_product.id, "quantity": 5, "discount": 0.0}])
    change_status(db_session, order.id, "processing")
    change_status(db_session, order.id, "done")
    change_status(db_session, order.id, "cancelled")
    p = db_session.get(Product, no_rest_product.id)
    assert p.rest == 0  # не вернули
