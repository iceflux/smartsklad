import pytest

from app.modules.inventory.models import Product
from app.modules.orders.service import (
    create_order, change_status, update_order, delete_order, restore_order,
    copy_order, list_orders, customer_order_stats,
)
from app.modules.customers.models import Customer
from app.modules.inventory.service import get_unit_by_name
from app.modules.orders.models import Order, OrderItem


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


@pytest.fixture
def product(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=50)
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def customer(db_session):
    c = Customer(name="ООО Строй", personal_discount=10.0)
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def plain_customer(db_session):
    c = Customer(name="ИП Петров")
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
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=5)
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


@pytest.fixture
def env(db_session):
    p1 = Product(name="A", sku="A-1", unit_id=_unit_id(db_session), price=10.0, rest=100)
    p2 = Product(name="B", sku="B-1", unit_id=_unit_id(db_session), price=20.0, rest=10)
    c = Customer(name="Клиент")
    db_session.add_all([p1, p2, c])
    db_session.commit()
    return db_session, p1, p2, c


def test_update_order_and_copy(env):
    db, p1, p2, c = env
    o = create_order(db, customer_id=c.id,
                     items=[{"product_id": p1.id, "quantity": 2, "discount": 0.0}])
    updated = update_order(db, o.id, items=[
        {"product_id": p2.id, "quantity": 3, "discount": 10.0},
    ])
    assert len(updated.items) == 1
    assert updated.items[0].product_name_snapshot == "B"
    assert updated.total_amount == round(3 * 20 * 0.9, 2)

    clone = copy_order(db, o.id)
    assert clone.id != o.id
    assert clone.status == "new"
    assert len(clone.items) == 1
    assert clone.items[0].quantity == 3
    assert clone.items[0].product_price_snapshot == 20.0
    assert clone.items[0].unit_snapshot == "шт"
    assert clone.total_amount == round(3 * 20 * 0.9, 2)


def test_list_and_stats(env):
    db, p1, p2, c = env
    o1 = create_order(db, customer_id=c.id,
                      items=[{"product_id": p1.id, "quantity": 5, "discount": 0.0}])  # 50
    o2 = create_order(db, customer_id=c.id,
                      items=[{"product_id": p1.id, "quantity": 1, "discount": 0.0}])  # 10
    change_status(db, o1.id, "processing")
    change_status(db, o1.id, "done")
    change_status(db, o2.id, "cancelled")
    items, total = list_orders(db, customer_id=c.id)
    assert total == 2
    items2, _ = list_orders(db, status="done")
    assert items2[0].id == o1.id
    stats = customer_order_stats(db, c.id)
    assert stats["orders_count"] == 1   # только не-cancelled
    assert stats["total_sum"] == 50.0
    assert stats["avg_check"] == 50.0

    delete_order(db, o1.id)
    items3, total3 = list_orders(db, customer_id=c.id)
    assert total3 == 1
    restore_order(db, o1.id)
    items4, total4 = list_orders(db, customer_id=c.id)
    assert total4 == 2


def test_update_done_forbidden(env):
    db, p1, _p2, c = env
    o = create_order(db, customer_id=c.id,
                     items=[{"product_id": p1.id, "quantity": 1, "discount": 0.0}])
    change_status(db, o.id, "processing")
    change_status(db, o.id, "done")
    with pytest.raises(ValueError):
        update_order(db, o.id, items=[{"product_id": p1.id, "quantity": 5, "discount": 0.0}])


def test_create_order_rejects_over_stock(db_session, product, customer):
    with pytest.raises(ValueError):
        create_order(db_session, customer_id=customer.id,
                     items=[{"product_id": product.id, "quantity": 51, "discount": 0.0}])
    assert product.rest == 50  # не тронули


def _imported_product(db_session, name, sku, unit_name="шт"):
    p = Product(name=name, sku=sku, price=0.0, rest=50,
                unit_id=get_unit_by_name(db_session, unit_name).id)
    db_session.add(p)
    db_session.flush()
    return p


def _imported_order(db_session, customer, lines):
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": p.id, "quantity": 1, "discount": 0.0}
                                for p, _price, _unit in lines])
    for item, (_p, price, unit) in zip(order.items, lines):
        item.product_price_snapshot = price
        item.unit_snapshot = unit
    db_session.flush()
    return order


def test_copying_an_order_carries_the_live_snapshots_of_the_source(
    db_session, plain_customer,
):
    product = _imported_product(db_session, "Гвозди", "NAIL-1")
    order = _imported_order(db_session, plain_customer, [(product, 100.0, "кг")])
    db_session.commit()

    clone = copy_order(db_session, order.id)

    assert clone.id != order.id
    assert clone.items[0].product_price_snapshot == 100.0
    assert clone.items[0].unit_snapshot == "кг"
    assert clone.total_amount == 100.0


def test_saving_an_order_keeps_the_price_snapshot_when_the_product_is_unchanged(
    db_session, plain_customer,
):
    product = _imported_product(db_session, "Гвозди", "NAIL-1")
    order = _imported_order(db_session, plain_customer, [(product, 100.0, "кг")])

    update_order(db_session, order.id, items=[
        {"product_id": product.id, "quantity": 3, "discount": 0.0},
    ])

    saved = db_session.get(Order, order.id)
    assert saved.items[0].product_price_snapshot == 100.0
    assert saved.items[0].unit_snapshot == "кг"
    assert saved.total_amount == 300.0


def test_saving_an_order_takes_the_snapshot_from_the_new_product_card(
    db_session, plain_customer,
):
    old_product = _imported_product(db_session, "Гвозди", "NAIL-1")
    new_product = _imported_product(db_session, "Шурупы", "SHUR-1")
    new_product.price = 7.5
    db_session.flush()
    assert new_product.id != old_product.id
    order = _imported_order(db_session, plain_customer, [(old_product, 100.0, "кг")])

    update_order(db_session, order.id, items=[
        {"product_id": new_product.id, "quantity": 2, "discount": 0.0},
    ])

    saved = db_session.get(Order, order.id)
    assert saved.items[0].product_id == new_product.id
    assert saved.items[0].product_name_snapshot == "Шурупы"
    assert saved.items[0].product_price_snapshot == 7.5
    assert saved.items[0].unit_snapshot == "шт"
    assert saved.total_amount == 15.0


def test_removing_a_middle_line_does_not_shift_snapshots_to_another_product(
    db_session, plain_customer,
):
    p1 = _imported_product(db_session, "Гвозди", "NAIL-1")
    p2 = _imported_product(db_session, "Шурупы", "SHUR-1")
    p3 = _imported_product(db_session, "Гайки", "GAYK-1")
    order = _imported_order(db_session, plain_customer, [
        (p1, 100.0, "шт"),
        (p2, 200.0, "кг"),
        (p3, 300.0, "т"),
    ])

    update_order(db_session, order.id, items=[
        {"product_id": p1.id, "quantity": 1, "discount": 0.0},
        {"product_id": p3.id, "quantity": 1, "discount": 0.0},
    ])

    saved = db_session.get(Order, order.id)
    assert [(it.product_id, it.product_price_snapshot, it.unit_snapshot)
            for it in saved.items] == [
        (p1.id, 100.0, "шт"),
        (p3.id, 300.0, "т"),
    ]
    assert saved.total_amount == 400.0


def test_repeated_product_takes_the_first_free_snapshot(db_session, plain_customer):
    product = _imported_product(db_session, "Гвозди", "NAIL-1")
    order = _imported_order(db_session, plain_customer, [(product, 100.0, "кг")])
    order.items.append(OrderItem(
        product_id=product.id,
        product_name_snapshot="Гвозди",
        product_price_snapshot=300.0,
        discount_snapshot=0.0,
        quantity=1,
        unit_snapshot="т",
    ))
    db_session.flush()
    assert len(order.items) == 2

    update_order(db_session, order.id, items=[
        {"product_id": product.id, "quantity": 1, "discount": 0.0},
    ])

    saved = db_session.get(Order, order.id)
    assert len(saved.items) == 1
    assert saved.items[0].product_price_snapshot == 100.0
    assert saved.items[0].unit_snapshot == "кг"


def test_saving_an_order_keeps_a_soft_deleted_client_unchanged(
    db_session, product, plain_customer,
):
    order = create_order(db_session, customer_id=plain_customer.id,
                         items=[{"product_id": product.id, "quantity": 1, "discount": 0.0}])
    plain_customer.is_deleted = True
    db_session.commit()

    saved = update_order(db_session, order.id, customer_id=plain_customer.id, items=[
        {"product_id": product.id, "quantity": 2, "discount": 0.0},
    ])

    assert saved.customer_id == plain_customer.id
    assert db_session.get(Order, order.id).items[0].quantity == 2


def test_saving_an_order_still_refuses_another_client(db_session, product, plain_customer):
    order = create_order(db_session, customer_id=plain_customer.id,
                         items=[{"product_id": product.id, "quantity": 1, "discount": 0.0}])
    gone = Customer(name="ООО Ликвидировано", is_deleted=True)
    db_session.add(gone)
    db_session.commit()
    plain_customer.is_deleted = True
    db_session.commit()
    lines = [{"product_id": product.id, "quantity": 1, "discount": 0.0}]

    for other_id in (gone.id, 9999):
        with pytest.raises(ValueError, match="customer not found"):
            update_order(db_session, order.id, customer_id=other_id, items=lines)
    assert db_session.get(Order, order.id).customer_id == plain_customer.id