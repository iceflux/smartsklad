import json

from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.customers.models import Customer
from app.modules.importer.files import DEFAULT_FOLDER, read_payloads
from app.modules.importer.models import ENTITY_CUSTOMER, ENTITY_ORDER, ENTITY_PRODUCT, ImportMap
from app.modules.importer.orders import resolve_orders
from app.modules.importer.resolve import (
    SERVICE_CUSTOMER_NAME,
    SERVICE_SOURCE_ID,
    SkuFactory,
    _map_index,
    resolve_customers,
    resolve_products,
    resolve_service_customer,
)
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.modules.orders.service import recalc_total
from app.modules.settings.service import get_setting, set_setting

IMPORT_PATH_KEY = "import_path"


def import_path(db: Session) -> str:
    return get_setting(db, IMPORT_PATH_KEY) or DEFAULT_FOLDER


def save_import_path(db: Session, value: str, *, user=None) -> None:
    text = (value or "").strip()
    if not text:
        raise ValueError("папка обязательна")
    set_setting(db, IMPORT_PATH_KEY, text, user=user)


def _existing(plans) -> dict:
    return {p.source_id: p.existing_id for p in plans if p.existing_id}


def _build(db: Session, folder, filenames) -> dict:
    sections = read_payloads(folder, filenames)
    product_plans, product_stats = resolve_products(db, sections["products"])
    customer_plans, customer_stats = resolve_customers(db, sections["customers"])
    order_plans, order_stats = resolve_orders(
        db, sections["orders"],
        _existing(product_plans), _existing(customer_plans),
        planned_products={p.source_id: (p.name, p.unit) for p in product_plans if p.create},
        planned_customers={p.source_id for p in customer_plans if p.create},
        imported_orders=set(_map_index(db, ENTITY_ORDER)),
    )
    service = resolve_service_customer(db) if order_stats["service_customer_planned"] else None
    return {
        "products": product_plans, "customers": customer_plans, "orders": order_plans,
        "service": service,
        "stats": {"products": product_stats, "customers": customer_stats, "orders": order_stats},
    }


def _report(built: dict, filenames) -> dict:
    stats = built["stats"]
    return {
        "files": list(filenames),
        "products": dict(stats["products"]),
        "customers": dict(stats["customers"]),
        "orders": dict(stats["orders"]),
        "service_customer_planned": built["service"] is not None,
        "service_customer_created": False,
        "warnings": list(stats["orders"]["warnings"])[:50],
    }


def plan_import(db: Session, folder, filenames) -> dict:
    return _report(_build(db, folder, filenames), filenames)


def _save_map(db: Session, entity_type: str, source_id: str, entity_id: int,
              label: str | None = None) -> None:
    db.add(ImportMap(entity_type=entity_type, source_id=source_id,
                     entity_id=entity_id, source_label=label))
    db.flush()


def _write_products(db: Session, plans) -> dict:
    created: dict = {}
    for plan in plans:
        if not plan.create:
            continue
        product = Product(name=plan.name, sku=plan.sku, unit=plan.unit,
                          price=0.0, rest=0, track_rest=True)
        db.add(product)
        db.flush()
        created[plan.source_id] = product.id
        _save_map(db, ENTITY_PRODUCT, plan.source_id, product.id, plan.name)
    return created


def _write_customers(db: Session, plans) -> dict:
    created: dict = {}
    for plan in plans:
        if not plan.create:
            continue
        customer = Customer(name=plan.name, inn=plan.inn, personal_discount=0.0)
        db.add(customer)
        db.flush()
        created[plan.source_id] = customer.id
        _save_map(db, ENTITY_CUSTOMER, plan.source_id, customer.id, plan.name)
    return created


def _item_product(db: Session, item, product_ids: dict, next_sku) -> tuple[int, str, str]:
    product_id = item.product_id
    if product_id is None and item.product_source_id is not None:
        product_id = product_ids.get(item.product_source_id)
    if product_id is not None:
        return product_id, item.product_name or item.name, item.product_unit or item.unit
    product = Product(name=item.name, sku=next_sku(), unit=item.unit,
                      price=0.0, rest=0, track_rest=True)
    db.add(product)
    db.flush()
    return product.id, product.name, product.unit


def _write_orders(db: Session, plans, *, service_id: int | None, product_ids: dict,
                  customer_ids: dict, next_sku) -> None:
    for plan in plans:
        customer_id = plan.customer_id
        if customer_id is None and plan.customer_source_id is not None:
            customer_id = customer_ids.get(plan.customer_source_id)
        if customer_id is None:
            customer_id = service_id
        if customer_id is None:
            continue
        order = Order(number=plan.number, customer_id=customer_id, status=plan.status,
                      discount=plan.discount, created_at=plan.created_at, deleted_at=None)
        for item in plan.items:
            product_id, name, unit = _item_product(db, item, product_ids, next_sku)
            order.items.append(OrderItem(
                product_id=product_id,
                product_name_snapshot=name,
                product_price_snapshot=item.price,
                discount_snapshot=0.0,
                quantity=item.quantity,
                unit_snapshot=unit,
            ))
        recalc_total(order)
        db.add(order)
        db.flush()
        _save_map(db, ENTITY_ORDER, plan.source_id, order.id, plan.source_number or None)


def _ensure_service_customer(db: Session, service) -> int | None:
    if service is None:
        return None
    if not service.create:
        return service.existing_id
    customer = Customer(name=SERVICE_CUSTOMER_NAME, personal_discount=0.0,
                        comment="Создан при импорте из 1С")
    db.add(customer)
    db.flush()
    _save_map(db, ENTITY_CUSTOMER, SERVICE_SOURCE_ID, customer.id, SERVICE_CUSTOMER_NAME)
    return customer.id


def run_import(db: Session, folder, filenames, *, user=None) -> dict:
    built = _build(db, folder, filenames)
    try:
        service_id = _ensure_service_customer(db, built["service"])
        product_ids = _write_products(db, built["products"])
        customer_ids = _write_customers(db, built["customers"])
        _write_orders(db, built["orders"], service_id=service_id, product_ids=product_ids,
                      customer_ids=customer_ids, next_sku=SkuFactory(db))
        report = _report(built, filenames)
        report["service_customer_created"] = bool(service_id) and built["service"].create
        log_action(db, action="import.run", entity_type="import", entity_id=None,
                   new_value=json.dumps(report, ensure_ascii=False),
                   user_id=user.id if user else None)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return report
