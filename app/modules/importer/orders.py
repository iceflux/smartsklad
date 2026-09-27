from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.customers.models import Customer
from app.modules.importer.resolve import _name_index, _unit, norm
from app.modules.inventory.models import Product
from app.modules.orders.service import next_order_number

STATUS_DONE = "done"
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
NEW_PRODUCT_PREFIX = "new:"


@dataclass
class ItemPlan:
    product_id: int | None
    name: str
    unit: str
    quantity: int
    price: float
    product_name: str | None = None
    product_unit: str = "шт"
    product_source_id: str | None = None


@dataclass
class OrderPlan:
    source_id: str
    source_number: str
    number: str
    customer_id: int | None
    status: str
    discount: float
    created_at: datetime
    items: list[ItemPlan] = field(default_factory=list)
    customer_source_id: str | None = None


def parse_order_date(value) -> datetime:
    raw = value.strip() if isinstance(value, str) else ""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).astimezone()
        except ValueError:
            continue
    raise ValueError(f"не удалось разобрать дату заказа: {raw!r}")


def _quantity(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _price(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _add(items: dict, key, item: ItemPlan) -> int:
    if key in items:
        existing = items[key]
        total = existing.quantity * existing.price + item.quantity * item.price
        existing.quantity += item.quantity
        existing.price = total / existing.quantity
        return 1
    items[key] = item
    return 0


def resolve_orders(
    db: Session,
    rows: list[dict],
    product_map: dict,
    customer_map: dict,
    *,
    planned_products: dict | None = None,
    planned_customers: set | None = None,
    imported_orders: set | None = None,
) -> tuple[list[OrderPlan], dict]:
    planned_products = planned_products or {}
    planned_customers = planned_customers or set()
    imported_orders = imported_orders or set()
    by_name = _name_index(db, Product)
    by_id = {p.id: p for p in db.query(Product).all()}
    discounts = dict(db.query(Customer.id, Customer.personal_discount).all())
    next_number = int(next_order_number(db))
    plans: list[OrderPlan] = []
    seen: set[str] = set()
    stats = {"created": 0, "matched": 0, "skipped": 0, "items_merged": 0, "empty_orders": 0,
             "service_customer_planned": 0, "products_from_items": 0, "warnings": []}
    for row in rows:
        source_id = (row.get("id") or "").strip()
        if not source_id or source_id in seen:
            stats["skipped"] += 1
            continue
        if source_id in imported_orders:
            stats["matched"] += 1
            continue
        try:
            created_at = parse_order_date(row.get("date"))
        except ValueError as err:
            stats["skipped"] += 1
            stats["warnings"].append(str(err))
            continue
        customer_source = (row.get("customer_id") or "").strip()
        customer_id = customer_map.get(customer_source) if customer_source else None
        items: dict = {}
        merged = 0
        broken = False
        for raw in row.get("items") or []:
            quantity = _quantity(raw.get("quantity"))
            if quantity < 1:
                stats["warnings"].append(
                    f"заказ {source_id}: позиция с количеством меньше 1 пропущена")
                continue
            item_source = (raw.get("product_id") or "").strip()
            line_name = (raw.get("name") or "").strip()
            unit = _unit(raw.get("unit"))
            price = _price(raw.get("price"))
            product_id = product_map.get(item_source) if item_source else None
            if product_id is not None:
                product = by_id.get(product_id)
                if product is None:
                    stats["warnings"].append(
                        f"заказ {source_id}: позиция ссылается на отсутствующий товар "
                        f"{item_source} — заказ пропущен")
                    broken = True
                    break
                key = product_id
                item = ItemPlan(
                    product_id=product_id, name=line_name, unit=unit,
                    quantity=quantity, price=price,
                    product_name=product.name, product_unit=product.unit)
            elif item_source and item_source in planned_products:
                key = NEW_PRODUCT_PREFIX + item_source
                planned_name, planned_unit = planned_products[item_source]
                item = ItemPlan(
                    product_id=None, name=line_name or planned_name, unit=unit,
                    quantity=quantity, price=price,
                    product_name=planned_name, product_unit=planned_unit,
                    product_source_id=item_source)
            elif not line_name:
                stats["warnings"].append(
                    f"заказ {source_id}: позиция без товара и без имени пропущена")
                broken = True
                break
            else:
                found = by_name.get(norm(line_name))
                if found is not None:
                    key = found.id
                    item = ItemPlan(
                        product_id=found.id, name=line_name, unit=unit,
                        quantity=quantity, price=price,
                        product_name=found.name, product_unit=found.unit)
                else:
                    key = NEW_PRODUCT_PREFIX + norm(line_name)
                    if key not in items:
                        stats["products_from_items"] += 1
                    item = ItemPlan(
                        product_id=None, name=line_name, unit=unit,
                        quantity=quantity, price=price)
            merged += _add(items, key, item)
        if broken:
            stats["skipped"] += 1
            continue
        seen.add(source_id)
        customer_source_id = None
        if customer_id is None and customer_source in planned_customers:
            customer_source_id = customer_source
        plans.append(OrderPlan(
            source_id=source_id,
            source_number=(row.get("number") or "").strip(),
            number=f"{next_number:05d}",
            customer_id=customer_id,
            status=STATUS_DONE,
            discount=discounts.get(customer_id, 0.0),
            created_at=created_at,
            items=list(items.values()),
            customer_source_id=customer_source_id,
        ))
        next_number += 1
        stats["created"] += 1
        stats["items_merged"] += merged
        if not items:
            stats["empty_orders"] += 1
        if customer_id is None and customer_source_id is None:
            stats["service_customer_planned"] += 1
    return plans, stats
