from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import ensure_permissions, get_current_user
from app.modules.auth.permissions import Permission
from app.modules.inventory.models import Category, PriceHistory, Product
from app.modules.inventory.service import (
    category_contents, change_bulk_category, create_category, create_product, delete_category,
    delete_product, export_products_csv, get_category_tree,
    list_products, restore_product, update_category, update_product,
)

router = APIRouter()

PERM_FIELD = {
    "rest": Permission.stock_change,
    "price": Permission.price_change,
}


def _ensure(user, *perms: Permission):
    ensure_permissions(user, *perms)


def _patch_permissions(fields: set[str]) -> list[Permission]:
    rights = {PERM_FIELD[f] for f in fields if f in PERM_FIELD}
    rights.update(Permission.products_edit for f in fields if f not in PERM_FIELD)
    return list(rights) if rights else [Permission.products_edit]


class CategoryIn(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None


class ProductIn(BaseModel):
    name: str
    sku: str
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    price: float = 0.0
    characteristic: Optional[dict] = None
    rest: int = 0
    track_rest: Optional[bool] = None
    min_stock: Optional[int] = None


class ProductPatch(BaseModel):
    name: Optional[str] = None
    sku: Optional[str] = None
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    price: Optional[float] = None
    characteristic: Optional[dict] = None
    rest: Optional[int] = None
    track_rest: Optional[bool] = None
    min_stock: Optional[int] = None


class BulkCategoryIn(BaseModel):
    product_ids: list[int]
    category_id: int


@router.get("/categories")
def api_categories(user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.products_view)
    return get_category_tree(db)


@router.post("/categories", status_code=201)
def api_create_category(payload: CategoryIn, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.categories_manage)
    name = payload.name or ""
    if not name.strip():
        raise HTTPException(status_code=400, detail="Название категории не может быть пустым")
    try:
        cat = create_category(db, name, payload.parent_id, user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _category_out(cat)


@router.patch("/categories/{category_id}")
def api_update_category(category_id: int, payload: CategoryIn, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.categories_manage)
    cat = db.get(Category, category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    name = payload.name if "name" in payload.model_fields_set else cat.name
    parent_id = payload.parent_id if "parent_id" in payload.model_fields_set else cat.parent_id
    if not (name or "").strip():
        raise HTTPException(status_code=400, detail="Название категории не может быть пустым")
    try:
        cat = update_category(db, category_id, name, parent_id, user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _category_out(cat)


@router.delete("/categories/{category_id}", status_code=204)
def api_delete_category(category_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.categories_manage)
    if db.get(Category, category_id) is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    try:
        delete_category(db, category_id, user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/categories/{category_id}/contents")
def api_category_contents(category_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.products_view)
    if db.get(Category, category_id) is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    return category_contents(db, category_id)


@router.get("/products")
def api_products(
    search: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    _ensure(user, Permission.products_view)
    items, total = list_products(db, search=search or None, category_id=category_id,
                                 page=page, page_size=page_size)
    return {"items": [_product_out(p) for p in items], "total": total, "page": page, "page_size": page_size}


@router.get("/products/export.csv")
def api_export_csv(user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.reports_export)
    content = export_products_csv(db)
    return Response(
        content=content,
        media_type="application/csv",
        headers={"Content-Disposition": 'attachment; filename="products.csv"'},
    )


@router.get("/products/{product_id}")
def api_product(product_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.products_view)
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    body = _product_out(product)
    body["category"] = _category_out(product.category) if product.category else None
    body["price_history"] = [
        {
            "old_price": r.old_price,
            "new_price": r.new_price,
            "changed_by": r.changed_by,
            "changed_by_name": r.changer.username if r.changer else None,
            "changed_at": r.changed_at.isoformat() if r.changed_at else None,
        }
        for r in db.query(PriceHistory)
        .filter(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.changed_at.desc())
        .all()
    ]
    return body


@router.post("/products", status_code=201)
def api_create_product(payload: ProductIn, user=Depends(get_current_user), db=Depends(get_db)):
    perms = [Permission.products_edit]
    if "rest" in payload.model_fields_set:
        perms.append(Permission.stock_change)
    if "price" in payload.model_fields_set:
        perms.append(Permission.price_change)
    _ensure(user, *perms)
    if not payload.name.strip() or not payload.sku.strip():
        raise HTTPException(status_code=400, detail="Название и артикул обязательны")
    if payload.min_stock is not None and payload.min_stock < 0:
        raise HTTPException(status_code=400, detail="Минимальный запас не может быть отрицательным")
    try:
        product = create_product(db, payload.name, payload.sku, payload.category_id,
                                 payload.unit_id, payload.price, payload.characteristic,
                                 payload.rest, track_rest=payload.track_rest,
                                 min_stock=payload.min_stock, user=user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _product_out(product)


@router.patch("/products/{product_id}")
def api_patch_product(product_id: int, payload: ProductPatch, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, *_patch_permissions(set(payload.model_fields_set)))
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if "name" in payload.model_fields_set and not (payload.name or "").strip():
        raise HTTPException(status_code=400, detail="Название товара не может быть пустым")
    if "sku" in payload.model_fields_set and not (payload.sku or "").strip():
        raise HTTPException(status_code=400, detail="Артикул не может быть пустым")
    if "min_stock" in payload.model_fields_set and payload.min_stock is not None and payload.min_stock < 0:
        raise HTTPException(status_code=400, detail="Минимальный запас не может быть отрицательным")
    try:
        product = update_product(db, product_id, payload.model_dump(exclude_unset=True), user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _product_out(product)


@router.delete("/products/{product_id}", status_code=204)
def api_delete_product(product_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.products_edit)
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    try:
        delete_product(db, product_id, user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/products/restore/{product_id}")
def api_restore_product(product_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.products_edit)
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    try:
        restore_product(db, product_id, user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _product_out(db.get(Product, product_id))


@router.post("/products/bulk-category")
def api_bulk_category(payload: BulkCategoryIn, user=Depends(get_current_user), db=Depends(get_db)):
    _ensure(user, Permission.products_edit)
    if db.get(Category, payload.category_id) is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    try:
        updated = change_bulk_category(db, payload.product_ids, payload.category_id, user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"updated": updated}


def _category_out(cat: Category) -> dict:
    return {
        "id": cat.id,
        "name": cat.name,
        "parent_id": cat.parent_id,
        "parent_name": cat.parent.name if cat.parent else None,
    }


def _product_out(product: Product) -> dict:
    category = product.category
    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku,
        "category_id": product.category_id,
        "category": _category_out(category) if category else None,
        "unit": product.unit,
        "unit_id": product.unit_id,
        "price": product.price,
        "characteristic": product.characteristic,
        "rest": product.rest,
        "min_stock": product.min_stock,
        "is_deleted": product.is_deleted,
        "track_rest": product.track_rest,
    }