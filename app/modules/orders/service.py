import json

from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem


def _validate_discount(value: float, field: str = "discount") -> float:
    value = float(value)
    if value < 0 or value > 99:
        raise ValueError(f"{field} must be between 0 and 99")
    return value


def _get_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None or customer.is_deleted:
        raise ValueError("customer not found")
    return customer


def _next_number(db: Session) -> str:
    last = db.query(Order).order_by(Order.id.desc()).first()
    num = int(last.number) + 1 if last else 1
    return f"{num:05d}"


def _recalc_total(order: Order) -> float:
    total = 0.0
    for it in order.items:
        total += it.quantity * it.product_price_snapshot * (1 - it.discount_snapshot / 100.0)
    total *= 1 - order.discount / 100.0
    order.total_amount = round(total, 2)
    return order.total_amount


def _order_dict(order: Order) -> dict:
    return {
        "number": order.number,
        "customer_id": order.customer_id,
        "status": order.status,
        "discount": order.discount,
        "total_amount": order.total_amount,
        "items": [
            {
                "product_id": it.product_id,
                "quantity": it.quantity,
                "discount": it.discount_snapshot,
            }
            for it in order.items
        ],
    }


def create_order(
    db: Session, *, customer_id: int, discount: float | None = None,
    items: list[dict], user=None,
) -> Order:
    customer = _get_customer(db, customer_id)
    order = Order(
        number=_next_number(db),
        customer_id=customer.id,
        status="new",
        discount=_validate_discount(customer.personal_discount if discount is None else discount),
    )
    db.add(order)
    _validate_items(db, items)  # минимально: товар существует, qty>=1, дубликатов нет
    for spec in items:
        product = db.get(Product, spec["product_id"])
        order.items.append(OrderItem(
            product_id=product.id,
            product_name_snapshot=product.name,
            product_price_snapshot=product.price,
            discount_snapshot=_validate_discount(spec["discount"], "item discount"),
            quantity=int(spec["quantity"]),
            unit_snapshot=product.unit,
        ))
    _recalc_total(order)
    db.commit()
    log_action(db, action="order.create", entity_type="order", entity_id=order.id,
               new_value=json.dumps(_order_dict(order), ensure_ascii=False),
               user_id=user.id if user else None)
    return order


def _validate_items(db: Session, items: list[dict]) -> None:
    product_ids = [i["product_id"] for i in items]
    if len(product_ids) != len(set(product_ids)):
        raise ValueError("duplicate product in items")
    for spec in items:
        product = db.get(Product, spec["product_id"])
        if product is None:
            raise ValueError("product not found")
        if int(spec["quantity"]) < 1:
            raise ValueError("quantity must be >= 1")