import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.sorting import apply_sort


VALID_STATUSES = {"new", "processing", "done", "cancelled"}
NEXT_POSSIBLE = {
    "new": {"processing", "cancelled"},
    "processing": {"done", "cancelled"},
    "done": {"processing", "cancelled"},
}


class OrderNotFoundError(ValueError):
    pass


class CustomerNotFoundError(ValueError):
    pass


def _validate_discount(value: float, field: str = "скидка") -> float:
    value = float(value)
    if value < 0 or value > 99:
        raise ValueError(f"{field} должна быть от 0 до 99")
    return value


def _get_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None or customer.is_deleted:
        raise CustomerNotFoundError("клиент не найден")
    return customer


def next_order_number(db: Session) -> str:
    last = db.query(Order).order_by(Order.id.desc()).first()
    num = int(last.number) + 1 if last else 1
    return f"{num:05d}"


def recalc_total(order: Order) -> float:
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
        number=next_order_number(db),
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
            product_price_snapshot=spec.get("price_snapshot", product.price),
            discount_snapshot=_validate_discount(spec["discount"], "скидка позиции"),
            quantity=int(spec["quantity"]),
            unit_snapshot=spec.get("unit_snapshot", product.unit),
        ))
    recalc_total(order)
    db.commit()
    log_action(db, action="order.create", entity_type="order", entity_id=order.id,
               new_value=json.dumps(_order_dict(order), ensure_ascii=False),
               user_id=user.id if user else None)
    return order


def _validate_items(db: Session, items: list[dict]) -> None:
    product_ids = [i["product_id"] for i in items]
    if len(product_ids) != len(set(product_ids)):
        raise ValueError("товар в заказе повторяется")
    for spec in items:
        product = db.get(Product, spec["product_id"])
        if product is None:
            raise ValueError("товар не найден")
        if int(spec["quantity"]) < 1:
            raise ValueError("количество должно быть не меньше 1")
        if product.track_rest and product.rest < int(spec["quantity"]):
            raise ValueError(
                f"не хватает остатка у товара {product.name}: "
                f"нужно {int(spec['quantity'])}, есть {product.rest}"
            )


def _check_and_writeoff(db: Session, order: Order) -> None:
    for it in order.items:
        product = db.get(Product, it.product_id)
        if not product.track_rest:
            continue
        if product.rest < it.quantity:
            raise ValueError(
                f"не хватает остатка у товара {it.product_name_snapshot}: "
                f"нужно {it.quantity}, есть {product.rest}"
            )
    for it in order.items:
        product = db.get(Product, it.product_id)
        if not product.track_rest:
            continue
        product.rest -= it.quantity


def _return_stock(db: Session, order: Order) -> None:
    for it in order.items:
        product = db.get(Product, it.product_id)
        if not product.track_rest:
            continue
        product.rest += it.quantity


def change_status(db: Session, order_id: int, new_status: str, *, user=None) -> Order:
    order = db.get(Order, order_id)
    if order is None or order.deleted_at is not None:
        raise OrderNotFoundError("заказ не найден")
    if new_status not in VALID_STATUSES:
        raise ValueError("неизвестный статус")
    if new_status == order.status:
        raise ValueError("статус не изменился")
    if new_status not in NEXT_POSSIBLE.get(order.status, set()):
        raise ValueError("недопустимый переход статуса")
    old_status = order.status
    if new_status == "done":
        _check_and_writeoff(db, order)
    elif old_status == "done":
        _return_stock(db, order)
    order.status = new_status
    db.commit()
    log_action(db, action="order.status_change", entity_type="order", entity_id=order.id,
               old_value=old_status, new_value=new_status, user_id=user.id if user else None)
    return order


def update_order(db, order_id, *, customer_id=None, discount=None, items=None, user=None):
    order = db.get(Order, order_id)
    if order is None or order.deleted_at is not None:
        raise OrderNotFoundError("заказ не найден")
    if order.status == "done":
        raise ValueError("нельзя редактировать выполненный заказ")
    old_value = json.dumps(_order_dict(order), ensure_ascii=False)
    if customer_id is not None and customer_id != order.customer_id:
        c = _get_customer(db, customer_id)
        order.customer_id = c.id
    if discount is not None:
        order.discount = _validate_discount(discount)
    if items is not None:
        if not items:
            raise ValueError("добавьте хотя бы одну позицию")
        _validate_items(db, items)
        previous: dict[int, list[tuple[float, str]]] = {}
        for old in order.items:
            previous.setdefault(old.product_id, []).append(
                (old.product_price_snapshot, old.unit_snapshot))
        order.items.clear()
        for spec in items:
            product = db.get(Product, spec["product_id"])
            bucket = previous.get(product.id)
            price, unit = bucket.pop(0) if bucket else (product.price, product.unit)
            order.items.append(OrderItem(
                product_id=product.id,
                product_name_snapshot=product.name,
                product_price_snapshot=price,
                discount_snapshot=_validate_discount(spec["discount"], "скидка позиции"),
                quantity=int(spec["quantity"]),
                unit_snapshot=unit,
            ))
    recalc_total(order)
    db.commit()
    new_value = json.dumps(_order_dict(order), ensure_ascii=False)
    log_action(db, action="order.update", entity_type="order", entity_id=order.id,
               old_value=old_value, new_value=new_value, user_id=user.id if user else None)
    return order


def delete_order(db, order_id, *, user=None):
    order = db.get(Order, order_id)
    if order is None or order.deleted_at is not None:
        raise OrderNotFoundError("заказ не найден")
    order.deleted_at = datetime.now()
    db.commit()
    log_action(db, action="order.delete", entity_type="order", entity_id=order_id,
               new_value=order.number, user_id=user.id if user else None)


def restore_order(db, order_id, *, user=None):
    order = db.get(Order, order_id)
    if order is None or order.deleted_at is None:
        raise OrderNotFoundError("заказ не найден")
    order.deleted_at = None
    db.commit()
    log_action(db, action="order.restore", entity_type="order", entity_id=order_id,
               new_value=order.number, user_id=user.id if user else None)


def copy_order(db, order_id, *, user=None):
    src = db.get(Order, order_id)
    if src is None:
        raise OrderNotFoundError("заказ не найден")
    items = [
        {"product_id": it.product_id, "quantity": it.quantity, "discount": it.discount_snapshot,
         "price_snapshot": it.product_price_snapshot, "unit_snapshot": it.unit_snapshot}
        for it in src.items
    ]
    return create_order(db, customer_id=src.customer_id,
                        discount=src.discount, items=items, user=user)


ORDER_SORTS = {
    "number": Order.number,
    "status": Order.status,
    "amount": Order.total_amount,
    "created": Order.created_at,
    "customer": Customer.name,
}
ORDER_JOINS = {
    "customer": (Customer, Order.customer_id == Customer.id),
}
"""У заказа нет relationships на клиента — в таблице лежит только `customer_id`,
а имя показывает роут, — поэтому присоединение пишется явным условием. Внешнее:
клиент в заказе обязателен, но строка могла бы остаться и без него, и внутреннее
присоединение убрало бы такой заказ из списка молча."""
DEFAULT_ORDER_SORT = "number"
"""Новые заказы сверху: так список был устроен до появления сортировки, и
менять порядок при загрузке страницы — значило бы менять его у того, кто его
уже знал."""
DEFAULT_ORDER_DIRECTION = "desc"


def list_orders(db, *, date_from=None, date_to=None, customer_id=None, status=None,
                min_amount=None, include_deleted=False, page=1, page_size=25,
                search=None, sort=None, direction=None):
    q = db.query(Order)
    if search:
        q = q.filter(Order.number.ilike(f"%{search}%"))
    if date_from is not None:
        q = q.filter(Order.created_at >= date_from)
    if date_to is not None:
        q = q.filter(Order.created_at <= date_to)
    if customer_id is not None:
        q = q.filter(Order.customer_id == customer_id)
    if status:
        q = q.filter(Order.status == status)
    if min_amount is not None:
        q = q.filter(Order.total_amount >= min_amount)
    if not include_deleted:
        q = q.filter(Order.deleted_at.is_(None))
    total = q.count()
    items = (apply_sort(q, Order, ORDER_SORTS, DEFAULT_ORDER_SORT,
                        DEFAULT_ORDER_DIRECTION, sort, direction, ORDER_JOINS)
             .offset((page - 1) * page_size).limit(page_size).all())
    return items, total


def get_order(db, order_id):
    return db.get(Order, order_id)


RECENT_WINDOW = 50
"""Сколько последних заказов смотрят, чтобы набрать 10 разных сущностей.
Десять заказов мало: один заказ может содержать один и тот же товар дважды, а
заказов без новых клиентов подряд бывает много. Окно взято с запасом, а не
«бесконечный просмотр таблицы»."""


def recent_customer_ids(db, limit: int) -> list[int]:
    """Клиенты последних заказов, свежие первыми.

    Заказов по пользователям в базе нет — таблица `orders` не помнит, кто их
    создал, а `create_order` принимает пользователя только для аудита. Поэтому
    «последние выбранные» — это последние заказы вообще, а не этого человека:
    придумывать принадлежность заказа значило бы либо врать, либо тащить миграцию
    с новой колонкой, чего владелец не просил.

    Мягко удалённые заказы не считаются: их позиции и клиент человек не выбирал.
    """
    rows = (db.query(Order.customer_id)
            .filter(Order.deleted_at.is_(None))
            .order_by(Order.id.desc()).limit(RECENT_WINDOW).all())
    return _first_distinct(rows, limit)


def recent_product_ids(db, limit: int) -> list[int]:
    """Товары последних заказов, свежие первыми. Порядок — по заказу, внутри
    заказа по строке, то есть последним выбранным в последнем заказе."""
    rows = (db.query(OrderItem.product_id)
            .join(Order, Order.id == OrderItem.order_id)
            .filter(Order.deleted_at.is_(None))
            .order_by(Order.id.desc(), OrderItem.id.desc())
            .limit(RECENT_WINDOW).all())
    return _first_distinct(rows, limit)


def _first_distinct(rows, limit: int) -> list[int]:
    """Разные значения по порядку строк, до `limit`. Список, а не множество:
    множество не умеет «свежие первыми»."""
    seen: list[int] = []
    for row in rows:
        value = row[0]
        if value in seen:
            continue
        seen.append(value)
        if len(seen) == limit:
            break
    return seen


def customer_order_stats(db, customer_id):
    q = db.query(Order).filter(
        Order.customer_id == customer_id,
        Order.deleted_at.is_(None),
        Order.status != "cancelled",
    )
    rows = q.all()
    total_sum = round(sum(o.total_amount for o in rows), 2)
    count = len(rows)
    return {"orders_count": count, "total_sum": total_sum,
            "avg_check": round(total_sum / count, 2) if count else 0.0}