from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import ensure_permissions, get_current_user
from app.modules.auth.permissions import Permission
from app.modules.customers.models import Customer
from app.modules.orders.models import Order
from app.modules.orders.service import (
    CustomerNotFound, OrderNotFound, change_status, copy_order, create_order,
    delete_order, list_orders, restore_order, update_order,
)

router = APIRouter()


class ItemIn(BaseModel):
    product_id: int
    quantity: int
    discount: float = 0.0


class OrderIn(BaseModel):
    customer_id: int
    discount: Optional[float] = None
    items: list[ItemIn]


class OrderPatch(BaseModel):
    customer_id: Optional[int] = None
    discount: Optional[float] = None
    items: Optional[list[ItemIn]] = None


class StatusIn(BaseModel):
    status: str


def _value_error_http(e: ValueError) -> HTTPException:
    if isinstance(e, (OrderNotFound, CustomerNotFound)):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=422, detail=str(e))


@router.get("/orders")
def api_orders(
    search: Optional[str] = Query(None),
    customer_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    min_amount: Optional[float] = Query(None),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    ensure_permissions(user, Permission.orders_view)
    df = datetime.combine(date_from, time.min) if date_from else None
    dt = datetime.combine(date_to, time.max) if date_to else None
    items, total = list_orders(
        db, search=search or None, customer_id=customer_id, status=status,
        date_from=df, date_to=dt, min_amount=min_amount,
        include_deleted=include_deleted, page=page, page_size=page_size,
    )
    return {"items": [_order_out(o, db) for o in items], "total": total,
            "page": page, "page_size": page_size}


@router.post("/orders", status_code=201)
def api_create_order(payload: OrderIn, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.orders_create)
    try:
        order = create_order(db, customer_id=payload.customer_id, discount=payload.discount,
                             items=[it.model_dump() for it in payload.items], user=user)
    except ValueError as e:
        raise _value_error_http(e)
    return _order_out(order, db)


@router.get("/orders/{order_id}")
def api_order(order_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.orders_view)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return _order_out(order, db)


@router.patch("/orders/{order_id}")
def api_patch_order(order_id: int, payload: OrderPatch,
                    user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.orders_edit)
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("items") is not None:
        fields["items"] = [it.model_dump() for it in fields["items"]]
    try:
        order = update_order(db, order_id, user=user, **fields)
    except ValueError as e:
        raise _value_error_http(e)
    return _order_out(order, db)


@router.delete("/orders/{order_id}", status_code=204)
def api_delete_order(order_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.orders_delete)
    try:
        delete_order(db, order_id, user=user)
    except ValueError as e:
        raise _value_error_http(e)


@router.post("/orders/{order_id}/restore")
def api_restore_order(order_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.orders_delete)
    try:
        restore_order(db, order_id, user=user)
    except ValueError as e:
        raise _value_error_http(e)
    return _order_out(db.get(Order, order_id), db)


@router.post("/orders/{order_id}/copy", status_code=201)
def api_copy_order(order_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.orders_create)
    try:
        order = copy_order(db, order_id, user=user)
    except ValueError as e:
        raise _value_error_http(e)
    return _order_out(order, db)


@router.post("/orders/{order_id}/status")
def api_order_status(order_id: int, payload: StatusIn,
                     user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.orders_status)
    try:
        order = change_status(db, order_id, payload.status, user=user)
    except ValueError as e:
        raise _value_error_http(e)
    return _order_out(order, db)


def _order_out(o: Order, db) -> dict:
    customer = db.get(Customer, o.customer_id)
    return {
        "id": o.id,
        "number": o.number,
        "customer_id": o.customer_id,
        "customer_name": customer.name if customer else "",
        "status": o.status,
        "discount": o.discount,
        "total_amount": o.total_amount,
        "items": [
            {
                "product_id": it.product_id,
                "product_name_snapshot": it.product_name_snapshot,
                "product_price_snapshot": it.product_price_snapshot,
                "discount_snapshot": it.discount_snapshot,
                "quantity": it.quantity,
                "unit_snapshot": it.unit_snapshot,
            }
            for it in o.items
        ],
        "deleted": o.deleted_at is not None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }