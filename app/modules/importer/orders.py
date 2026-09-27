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


def _add(items: dict, key, item: ItemPlan, quantity: int) -> int:
    if key in items:
        items[key].quantity += quantity
        return 1
    items[key] = item
    return 0


def resolve_orders(
    db: Session,
    rows: list[dict],
    product_map: dict,
    customer_map: dict,
) -> tuple[list[OrderPlan], dict]:
    by_name = _name_index(db, Product)
    by_id = {p.id: p for p in db.query(Product).all()}
    discounts = dict(db.query(Customer.id, Customer.personal_discount).all())
    next_number = int(next_order_number(db))
    plans: list[OrderPlan] = []
    seen: set[str] = set()
    stats = {"created": 0, "skipped": 0, "items_merged": 0, "empty_orders": 0,
             "service_customer_planned": 0, "products_from_items": 0, "warnings": []}
    for row in rows:
        source_id = (row.get("id") or "").strip()
        if not source_id or source_id in seen:
            stats["skipped"] += 1
            continue
        try:
            created_at = parse_order_date(row.get("date"))
        except ValueError as err:
            stats["skipped"] += 1
            stats["warnings"].append(str(err))
            continue
        customer_id = customer_map.get((row.get("customer_id") or "").strip())
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
            product_id = product_map.get(item_source) if item_source else None
            if product_id is None:
                name = (raw.get("name") or "").strip()
                if not name:
                    stats["warnings"].append(
                        f"заказ {source_id}: позиция без товара и без имени пропущена")
                    broken = True
                    break
                found = by_name.get(norm(name))
                if found is not None:
                    product_id = found.id
                else:
                    stats["products_from_items"] += 1
                    key = NEW_PRODUCT_PREFIX + norm(name)
                    merged += _add(items, key, ItemPlan(
                        None, name, _unit(raw.get("unit")), quantity, _price(raw.get("price"))),
                        quantity)
                    continue
            product = by_id.get(product_id)
            merged += _add(items, product_id, ItemPlan(
                product_id=product_id,
                name=(raw.get("name") or "").strip(),
                unit=_unit(raw.get("unit")),
                quantity=quantity,
                price=_price(raw.get("price")),
                product_name=product.name if product else None,
                product_unit=product.unit if product else "шт",
            ), quantity)
        if broken:
            stats["skipped"] += 1
            continue
        seen.add(source_id)
        plans.append(OrderPlan(
            source_id=source_id,
            source_number=(row.get("number") or "").strip(),
            number=f"{next_number:05d}",
            customer_id=customer_id,
            status=STATUS_DONE,
            discount=discounts.get(customer_id, 0.0),
            created_at=created_at,
            items=list(items.values()),
        ))
        next_number += 1
        stats["created"] += 1
        stats["items_merged"] += merged
        if not items:
            stats["empty_orders"] += 1
        if customer_id is None:
            stats["service_customer_planned"] += 1
    return plans, stats
