import pytest

from app.modules.inventory.models import Product
from app.modules.orders.service import create_order, change_status
from app.modules.customers.models import Customer


@pytest.fixture
def product(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=100.0, rest=50)
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def customer(db_session):
    c = Customer(name="ООО Строй", personal_discount=10.0)
    db_session.add(c)
    db_session.commit()
    return c


def test_create_order_snapshot_and_total(db_session, product, customer):
    order = create_order(
        db_session, customer_id=customer.id,
        items=[{"product_id": product.id, "quantity": 2, "discount": 0.0}],
    )
    assert order.number == "00001"
    assert order.customer_id == customer.id
    assert order.status == "new"
    # вариант: скидка заказа из personal_discount клиента по умолчанию
    assert order.discount == 10.0
    assert order.total_amount == 180.0  # 2*100 * 0.9
    assert len(order.items) == 1
    it = order.items[0]
    assert it.product_name_snapshot == "Гвозди"
    assert it.product_price_snapshot == 100.0


def test_create_order_next_number(db_session, product, customer):
    create_order(db_session, customer_id=customer.id,
                 items=[{"product_id": product.id, "quantity": 1, "discount": 0.0}])
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": product.id, "quantity": 1, "discount": 0.0}])
    assert order.number == "00002"


def test_create_order_invalid_customer(db_session, product):
    with pytest.raises(ValueError):
        create_order(db_session, customer_id=999,
                     items=[{"product_id": product.id, "quantity": 1, "discount": 0.0}])


@pytest.fixture
def setup(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=100.0, rest=5)
    c = Customer(name="ООО Строй")
    db_session.add_all([p, c])
    db_session.commit()
    order = create_order(db_session, customer_id=c.id,
                         items=[{"product_id": p.id, "quantity": 3, "discount": 0.0}])
    return db_session, p, c, order


def test_status_flow_writes_off_and_returns(setup):
    db, product, _c, order = setup
    change_status(db, order.id, "processing")
    assert product.rest == 5  # ещё не списали
    change_status(db, order.id, "done")
    assert db.get(Product, product.id).rest == 2  # списали 3
    change_status(db, order.id, "cancelled")
    assert db.get(Product, product.id).rest == 5  # вернули


def test_status_done_insufficient_stock_rejected(setup):
    db, product, _c, order = setup
    change_status(db, order.id, "processing")
    product.rest = 1
    db.commit()
    with pytest.raises(ValueError):
        change_status(db, order.id, "done")
    assert db.get(Product, product.id).rest == 1  # не тронули


def test_forbidden_transition(setup):
    db, _p, _c, order = setup
    with pytest.raises(ValueError):
        change_status(db, order.id, "done")  # new -> done нельзя