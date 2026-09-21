import csv
import io
import json

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.inventory.models import Category, PriceHistory, Product


def create_category(db: Session, name: str, parent_id: int | None = None, user=None) -> Category:
    name = name.strip()
    if not name:
        raise ValueError("name required")
    existing = db.query(Category).filter(
        Category.name == name,
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


def delete_category(db: Session, category_id: int, user=None) -> None:
    cat = db.get(Category, category_id)
    if cat is None:
        raise ValueError("category not found")
    has_children = db.query(Category).filter(Category.parent_id == cat.id).first() is not None
    has_products = db.query(Product).filter(Product.category_id == cat.id).first() is not None
    if has_children or has_products:
        raise ValueError("category not empty")
    cat.is_deleted = True
    db.commit()
    log_action(db, action="category.delete", entity_type="category", entity_id=cat.id,
               new_value=cat.name, user_id=user.id if user else None)


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


def create_product(
    db: Session,
    name: str,
    sku: str,
    category_id: int | None,
    unit: str,
    price: float,
    characteristic: dict | None,
    rest: int,
    user=None,
) -> Product:
    name = name.strip()
    if not name:
        raise ValueError("name required")
    sku = sku.strip()
    dup = db.query(Product).filter(Product.sku == sku).first()
    if dup:
        raise ValueError("duplicate sku")
    product = Product(
        name=name,
        sku=sku,
        category_id=category_id,
        unit=unit,
        price=price,
        characteristic=characteristic,
        rest=rest,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    log_action(db, action="product.create", entity_type="product", entity_id=product.id,
               new_value=name, user_id=user.id if user else None)
    return product


def update_product(db: Session, product_id: int, data: dict, user=None) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError("product not found")
    old_dict = _product_dict(product)
    if "sku" in data:
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
    for key in ("category_id", "unit", "characteristic", "rest"):
        if key in data:
            setattr(product, key, data[key])
    old_value = json.dumps(old_dict, ensure_ascii=False)
    db.commit()
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


def list_products(
    db: Session,
    search: str | None = None,
    category_id: int | None = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = 25,
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
    items = (
        query.order_by(Product.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
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
        "characteristic": product.characteristic,
        "rest": product.rest,
    }