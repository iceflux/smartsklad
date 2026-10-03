import math
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.customers.models import Customer
from app.modules.importer.models import ENTITY_CUSTOMER, ENTITY_PRODUCT, ImportMap
from app.modules.inventory.models import Product

SERVICE_CUSTOMER_NAME = "Без контрагента (1С)"
SERVICE_SOURCE_ID = "service"
SKU_PREFIX = "1C-"


@dataclass
class ProductPlan:
    source_id: str
    name: str
    unit: str
    existing_id: int | None
    create: bool
    sku: str | None = None
    price: float = 0.0
    rest: int = 0


@dataclass
class CustomerPlan:
    source_id: str
    name: str
    inn: str | None
    create: bool
    existing_id: int | None = None


def norm(value: str | None) -> str:
    return (value or "").strip().lower()


def clean_inn(value) -> str | None:
    raw = value.strip() if isinstance(value, str) else ""
    return raw if raw.isdigit() and len(raw) in (10, 12) else None


def _map_index(db: Session, entity_type: str) -> dict[str, int]:
    rows = db.query(ImportMap).filter(ImportMap.entity_type == entity_type).all()
    return {r.source_id: r.entity_id for r in rows}


def _name_index(db: Session, model) -> dict[str, object]:
    index: dict[str, object] = {}
    rows = db.query(model).filter(model.is_deleted.is_(False)).order_by(model.id).all()
    for row in rows:
        index.setdefault(norm(row.name), row)
    return index


def _unit(value) -> str:
    raw = value.strip() if isinstance(value, str) else ""
    return raw or "шт"


def _price(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _rest(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number >= 0 else 0


class SkuFactory:
    def __init__(self, db: Session, prefix: str = SKU_PREFIX) -> None:
        self._taken = {sku for (sku,) in db.query(Product.sku).all()}
        self._counter = 0
        self._prefix = prefix

    def __call__(self) -> str:
        while True:
            self._counter += 1
            candidate = f"{self._prefix}{self._counter:05d}"
            if candidate not in self._taken:
                self._taken.add(candidate)
                return candidate


def resolve_products(db: Session, rows: list[dict]) -> tuple[list[ProductPlan], dict]:
    by_source = _map_index(db, ENTITY_PRODUCT)
    by_name = _name_index(db, Product)
    next_sku = SkuFactory(db)
    plans: list[ProductPlan] = []
    seen: set[str] = set()
    stats = {"created": 0, "matched": 0, "skipped": 0}
    for row in rows:
        source_id = (row.get("id") or "").strip()
        name = (row.get("name") or "").strip()
        if not source_id or not name or source_id in seen:
            stats["skipped"] += 1
            continue
        seen.add(source_id)
        unit = _unit(row.get("unit"))
        if source_id in by_source:
            plans.append(ProductPlan(source_id, name, unit, by_source[source_id], False))
            stats["matched"] += 1
            continue
        found = by_name.get(norm(name))
        if found is not None:
            plans.append(ProductPlan(source_id, name, unit, found.id, False))
            stats["matched"] += 1
            continue
        plans.append(ProductPlan(source_id, name, unit, None, True, next_sku(),
                                 _price(row.get("price")), _rest(row.get("rest"))))
        stats["created"] += 1
    return plans, stats


def resolve_customers(db: Session, rows: list[dict]) -> tuple[list[CustomerPlan], dict]:
    by_source = _map_index(db, ENTITY_CUSTOMER)
    by_name = _name_index(db, Customer)
    plans: list[CustomerPlan] = []
    seen: set[str] = set()
    stats = {"created": 0, "matched": 0, "inn_dropped": 0, "skipped": 0}
    for row in rows:
        source_id = (row.get("id") or "").strip()
        name = (row.get("name") or "").strip()
        if not source_id or not name or source_id in seen:
            stats["skipped"] += 1
            continue
        seen.add(source_id)
        raw_inn = row.get("inn")
        inn = clean_inn(raw_inn)
        if inn is None and raw_inn is not None and str(raw_inn).strip():
            stats["inn_dropped"] += 1
        if source_id in by_source:
            plans.append(CustomerPlan(source_id, name, inn, False, by_source[source_id]))
            stats["matched"] += 1
            continue
        found = by_name.get(norm(name))
        if found is not None:
            plans.append(CustomerPlan(source_id, name, inn, False, found.id))
            stats["matched"] += 1
            continue
        plans.append(CustomerPlan(source_id, name, inn, True, None))
        stats["created"] += 1
    return plans, stats


def resolve_service_customer(db: Session) -> CustomerPlan | None:
    found = db.query(Customer).filter(
        Customer.name == SERVICE_CUSTOMER_NAME,
        Customer.is_deleted.is_(False),
    ).first()
    if found is not None:
        return CustomerPlan(SERVICE_SOURCE_ID, SERVICE_CUSTOMER_NAME, None, False, found.id)
    return CustomerPlan(SERVICE_SOURCE_ID, SERVICE_CUSTOMER_NAME, None, True, None)
