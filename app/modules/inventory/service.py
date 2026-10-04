import csv
import io
import json

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.importer.resolve import SkuFactory
from app.modules.inventory.models import Category, PriceHistory, Product, Unit
from app.modules.settings.service import STOCK_TRACK_DEFAULT_KEY, get_setting
from app.sorting import apply_sort

DEFAULT_UNITS = (
    "шт", "кг", "г", "т", "л", "мл", "м", "м2", "м3", "см", "мм",
    "упак", "ящ", "короб", "пач", "пакет", "бут", "бан", "вед", "пал",
    "пара", "компл", "набор", "рул", "ед", "ч", "мин", "сут", "дюжина",
    "лист", "кат", "дюйм", "фут", "галл",
)
UNIT_NAME_MAX = 32
DEFAULT_UNIT = "шт"


def ensure_default_units(db: Session) -> None:
    existing = {row.name for row in db.query(Unit.name).all()}
    for name in DEFAULT_UNITS:
        if name not in existing:
            db.add(Unit(name=name))
    db.commit()


def create_category(db: Session, name: str, parent_id: int | None = None, user=None) -> Category:
    name = name.strip()
    if not name:
        raise ValueError("name required")
    existing = db.query(Category).filter(
        Category.name == name,
        Category.is_deleted.is_(False),
        Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
    ).first()
    if existing:
        raise ValueError("duplicate name")
    cat = Category(name=name, parent_id=parent_id)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    if user:
        log_action(db, action="category.create", entity_type="category", entity_id=cat.id,
                   new_value=name, user_id=user.id)
    return cat


def update_category(db: Session, category_id: int, name: str, parent_id: int | None, user=None) -> Category:
    cat = db.get(Category, category_id)
    if cat is None:
        raise ValueError("category not found")
    name = name.strip()
    if not name:
        raise ValueError("name required")
    if parent_id is not None and (parent_id == cat.id or parent_id in _descendant_ids(db, cat.id)):
        raise ValueError("category cannot be its own parent")
    dup = db.query(Category).filter(
        Category.id != cat.id,
        Category.name == name,
        Category.is_deleted.is_(False),
        Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
    ).first()
    if dup:
        raise ValueError("duplicate name")
    old_value = json.dumps({"name": cat.name, "parent_id": cat.parent_id}, ensure_ascii=False)
    cat.name = name
    cat.parent_id = parent_id
    db.commit()
    new_value = json.dumps({"name": cat.name, "parent_id": cat.parent_id}, ensure_ascii=False)
    log_action(db, action="category.update", entity_type="category", entity_id=cat.id,
               old_value=old_value, new_value=new_value, user_id=user.id if user else None)
    return cat


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def category_contents(db: Session, category_id: int) -> dict:
    return {
        "products": db.query(Product).filter(
            Product.category_id == category_id, Product.is_deleted.is_(False)
        ).count(),
        "children": db.query(Category).filter(
            Category.parent_id == category_id, Category.is_deleted.is_(False)
        ).count(),
    }


def _not_empty_message(counts: dict) -> str:
    parts = []
    if counts["products"]:
        n = counts["products"]
        parts.append(f"{n} {_plural(n, 'товар', 'товара', 'товаров')}")
    if counts["children"]:
        n = counts["children"]
        parts.append(f"{n} {_plural(n, 'подкатегория', 'подкатегории', 'подкатегорий')}")
    return (
        "нельзя удалить категорию: в ней " + " и ".join(parts)
        + ". Перенесите или удалите содержимое."
    )


def delete_category(db: Session, category_id: int, user=None, *, resolve: bool = False,
                    reassign_to: int | None = None) -> None:
    cat = db.get(Category, category_id)
    if cat is None:
        raise ValueError("category not found")
    counts = category_contents(db, cat.id)
    blocked = bool(counts["products"] or counts["children"])
    if blocked and not resolve:
        raise ValueError(_not_empty_message(counts))
    target = None
    moved_products = moved_children = 0
    if blocked:
        destination = None
        if reassign_to is not None:
            target = db.get(Category, reassign_to)
            if target is None or target.is_deleted:
                raise ValueError("категория назначения не найдена")
            if reassign_to == cat.id or reassign_to in _descendant_ids(db, cat.id):
                raise ValueError("нельзя перенести категорию в саму себя или в своего потомка")
            destination = target.id
        moved_products = (
            db.query(Product)
            .filter(Product.category_id == cat.id, Product.is_deleted.is_(False))
            .update({Product.category_id: destination}, synchronize_session=False)
        )
        moved_children = (
            db.query(Category)
            .filter(Category.parent_id == cat.id, Category.is_deleted.is_(False))
            .update({Category.parent_id: destination}, synchronize_session=False)
        )
    cat.is_deleted = True
    db.commit()
    log_action(db, action="category.delete", entity_type="category", entity_id=cat.id,
               new_value=cat.name, user_id=user.id if user else None)
    if target is not None:
        log_action(db, action="category.reassign", entity_type="category", entity_id=cat.id,
                   new_value=json.dumps({"to": target.id, "products": moved_products,
                                         "children": moved_children}, ensure_ascii=False),
                   user_id=user.id if user else None)


def list_categories(db: Session) -> list[Category]:
    return (
        db.query(Category)
        .filter(Category.is_deleted.is_(False))
        .order_by(Category.name)
        .all()
    )


def get_category_tree(db: Session) -> list[dict]:
    roots = (
        db.query(Category)
        .filter(Category.parent_id.is_(None), Category.is_deleted.is_(False))
        .order_by(Category.name)
        .all()
    )
    return [
        {
            "id": root.id,
            "name": root.name,
            "parent_id": root.parent_id,
            "children": [
                {"id": c.id, "name": c.name}
                for c in db.query(Category)
                .filter(Category.parent_id == root.id, Category.is_deleted.is_(False))
                .order_by(Category.name)
                .all()
            ],
        }
        for root in roots
    ]


ARTICLE_ATTEMPTS = 5


def next_sku(db: Session) -> str:
    return SkuFactory(db, prefix="")()


def _assign_sku(db: Session, product_id: int) -> str:
    for _ in range(ARTICLE_ATTEMPTS):
        product = db.get(Product, product_id)
        if product is None:
            raise ValueError("product not found")
        sku = next_sku(db)
        product.sku = sku
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        return sku
    raise ValueError("не удалось подобрать свободный артикул")


def create_product(
    db: Session,
    name: str,
    sku: str,
    category_id: int | None,
    unit_id: int | None,
    price: float,
    characteristic: dict | None,
    rest: int,
    *,
    track_rest: bool | None = None,
    min_stock: int | None = None,
    description: str | None = None,
    user=None,
) -> Product:
    name = name.strip()
    if not name:
        raise ValueError("name required")
    sku = sku.strip()
    auto_sku = not sku
    if not auto_sku and db.query(Product).filter(Product.sku == sku).first():
        raise ValueError("duplicate sku")
    if min_stock is not None and min_stock < 0:
        raise ValueError("min_stock must be >= 0")
    unit = db.get(Unit, unit_id) if unit_id is not None else get_unit_by_name(db, DEFAULT_UNIT)
    if unit is None or unit.is_deleted:
        raise ValueError("единица не найдена")
    fields = dict(
        name=name,
        category_id=category_id,
        unit_id=unit.id,
        price=price,
        characteristic=characteristic,
        description=(description or "").strip() or None,
        rest=rest,
        min_stock=min_stock,
        track_rest=track_rest if track_rest is not None else (get_setting(db, STOCK_TRACK_DEFAULT_KEY) != "false"),
    )
    for _ in range(ARTICLE_ATTEMPTS):
        product = Product(sku=(next_sku(db) if auto_sku else sku), **fields)
        db.add(product)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if not auto_sku:
                raise ValueError("duplicate sku")
            continue
        db.refresh(product)
        log_action(db, action="product.create", entity_type="product", entity_id=product.id,
                   new_value=name, user_id=user.id if user else None)
        return product
    raise ValueError("не удалось подобрать свободный артикул")


def update_product(db: Session, product_id: int, data: dict, user=None) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError("product not found")
    if "min_stock" in data and data["min_stock"] is not None and data["min_stock"] < 0:
        raise ValueError("min_stock must be >= 0")
    if "unit_id" in data:
        unit = db.get(Unit, data["unit_id"]) if data["unit_id"] is not None else get_unit_by_name(db, DEFAULT_UNIT)
        if unit is None or unit.is_deleted:
            raise ValueError("единица не найдена")
        data = {**data, "unit_id": unit.id}
    old_dict = _product_dict(product)
    blank_sku = "sku" in data and not (data["sku"] or "").strip()
    if "sku" in data and not blank_sku:
        new_sku = data["sku"].strip()
        if new_sku != product.sku:
            dup = db.query(Product).filter(Product.sku == new_sku, Product.id != product.id).first()
            if dup:
                raise ValueError("duplicate sku")
            product.sku = new_sku
    if "name" in data:
        product.name = data["name"].strip()
    old_price = product.price
    if "price" in data and data["price"] != old_price:
        db.add(PriceHistory(
            product_id=product.id,
            old_price=old_price,
            new_price=data["price"],
            changed_by=user.id if user else None,
        ))
        product.price = data["price"]
    for key in ("category_id", "unit_id", "characteristic", "rest", "track_rest", "min_stock"):
        if key in data:
            setattr(product, key, data[key])
    if "description" in data:
        product.description = (data["description"] or "").strip() or None
    old_value = json.dumps(old_dict, ensure_ascii=False)
    db.commit()
    if blank_sku:
        _assign_sku(db, product_id)
        product = db.get(Product, product_id)
    new_value = json.dumps(_product_dict(product), ensure_ascii=False)
    log_action(db, action="product.update", entity_type="product", entity_id=product.id,
               old_value=old_value, new_value=new_value, user_id=user.id if user else None)
    return product


def delete_product(db: Session, product_id: int, user=None) -> None:
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError("product not found")
    product.is_deleted = True
    db.commit()
    log_action(db, action="product.delete", entity_type="product", entity_id=product_id,
               new_value=product.name, user_id=user.id if user else None)


def restore_product(db: Session, product_id: int, user=None) -> None:
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError("product not found")
    product.is_deleted = False
    db.commit()
    log_action(db, action="product.restore", entity_type="product", entity_id=product_id,
               new_value=product.name, user_id=user.id if user else None)


PRODUCT_SORTS = {
    "name": Product.name,
    "sku": Product.sku,
    "price": Product.price,
    "rest": Product.rest,
}
DEFAULT_PRODUCT_SORT = "name"
DEFAULT_PRODUCT_DIRECTION = "asc"


def list_products(
    db: Session,
    search: str | None = None,
    category_id: int | None = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str | None = None,
) -> tuple[list[Product], int]:
    query = db.query(Product)
    if search:
        query = query.filter(
            or_(Product.name.ilike(f"%{search}%"), Product.sku.ilike(f"%{search}%"))
        )
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    if not include_deleted:
        query = query.filter(Product.is_deleted.is_(False))
    total = query.count()
    items = (apply_sort(query, Product, PRODUCT_SORTS, DEFAULT_PRODUCT_SORT,
                        DEFAULT_PRODUCT_DIRECTION, sort, direction)
             .offset((page - 1) * page_size)
             .limit(page_size)
             .all())
    return items, total


def change_bulk_category(db: Session, product_ids: list[int], category_id: int, user=None) -> int:
    category = db.get(Category, category_id)
    if category is None:
        raise ValueError("category not found")
    products = (
        db.query(Product)
        .filter(Product.id.in_(product_ids), Product.is_deleted.is_(False))
        .all()
    )
    for product in products:
        product.category_id = category_id
    db.commit()
    log_action(db, action="product.bulk_category", entity_type="product",
               new_value=str(category_id), user_id=user.id if user else None)
    return len(products)


def export_products_csv(db: Session) -> bytes:
    products = (
        db.query(Product)
        .filter(Product.is_deleted.is_(False))
        .order_by(Product.name)
        .all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["name", "sku", "category", "unit", "price", "rest"])
    for product in products:
        category = product.category
        if category is None:
            category_name = ""
        elif category.parent is not None:
            category_name = f"{category.parent.name} / {category.name}"
        else:
            category_name = category.name
        writer.writerow([product.name, product.sku, category_name,
                         product.unit, product.price, product.rest])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def get_product(db: Session, product_id: int) -> Product | None:
    return db.get(Product, product_id)


def _descendant_ids(db: Session, category_id: int) -> list[int]:
    result = []
    to_visit = [
        c.id
        for c in db.query(Category)
        .filter(Category.parent_id == category_id, Category.is_deleted.is_(False))
        .all()
    ]
    while to_visit:
        current = to_visit.pop()
        result.append(current)
        to_visit += [
            c.id
            for c in db.query(Category)
            .filter(Category.parent_id == current, Category.is_deleted.is_(False))
            .all()
        ]
    return result


def _product_dict(product: Product) -> dict:
    return {
        "name": product.name,
        "sku": product.sku,
        "category_id": product.category_id,
        "unit": product.unit,
        "price": product.price,
        "description": product.description,
        "characteristic": product.characteristic,
        "rest": product.rest,
        "min_stock": product.min_stock,
        "track_rest": product.track_rest,
    }


def _clean_unit_name(name) -> str:
    return name.strip() if isinstance(name, str) else ""


def list_units(db: Session) -> list[Unit]:
    return (
        db.query(Unit)
        .filter(Unit.is_deleted.is_(False))
        .order_by(Unit.name)
        .all()
    )


def get_unit_by_name(db: Session, name) -> Unit | None:
    return (
        db.query(Unit)
        .filter(Unit.is_deleted.is_(False))
        .filter(func.lower(Unit.name) == _clean_unit_name(name).lower())
        .first()
    )


def _require_free_unit_name(db: Session, name: str) -> str:
    text = _clean_unit_name(name)
    if not text:
        raise ValueError("название единицы обязательно")
    if len(text) > UNIT_NAME_MAX:
        raise ValueError(f"название длиннее {UNIT_NAME_MAX} символов")
    if get_unit_by_name(db, text) is not None:
        raise ValueError("единица с таким названием уже есть")
    return text


def create_unit(db: Session, name, *, user=None, commit: bool = True) -> Unit:
    unit = Unit(name=_require_free_unit_name(db, name))
    db.add(unit)
    if commit:
        db.commit()
    else:
        db.flush()
    log_action(db, action="unit.create", entity_type="unit", entity_id=unit.id,
               new_value=unit.name, user_id=user.id if user else None, commit=commit)
    return unit


def update_unit(db: Session, unit_id: int, name, *, user=None) -> Unit:
    unit = db.get(Unit, unit_id)
    if unit is None or unit.is_deleted:
        raise ValueError("единица не найдена")
    new_name = _require_free_unit_name(db, name)
    old_name = unit.name
    unit.name = new_name
    db.commit()
    log_action(db, action="unit.update", entity_type="unit", entity_id=unit.id,
               old_value=old_name, new_value=new_name, user_id=user.id if user else None)
    return unit


def delete_unit(db: Session, unit_id: int, *, user=None) -> None:
    unit = db.get(Unit, unit_id)
    if unit is None or unit.is_deleted:
        raise ValueError("единица не найдена")
    in_use = db.query(Product.id).filter(Product.unit_id == unit.id).first() is not None
    if in_use:
        raise ValueError("единица используется товарами")
    unit.is_deleted = True
    db.commit()
    log_action(db, action="unit.delete", entity_type="unit", entity_id=unit.id,
               new_value=unit.name, user_id=user.id if user else None)