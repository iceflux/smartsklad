from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem


def test_order_models(db_session):
    product = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=10.0, rest=100)
    db_session.add(product)
    db_session.commit()
    order = Order(number="00001", customer_id=1, status="new", discount=0.0, total_amount=100.0)
    db_session.add(order)
    db_session.commit()
    item = OrderItem(
        order_id=order.id, product_id=product.id,
        product_name_snapshot=product.name, product_price_snapshot=product.price,
        discount_snapshot=0.0, quantity=10, unit_snapshot=product.unit,
    )
    db_session.add(item)
    db_session.commit()
    assert len(order.items) == 1
    assert order.customer_id == 1
    assert order.items[0].product_name_snapshot == "Гвозди"