from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import ensure_permissions, get_current_user
from app.modules.auth.permissions import Permission
from app.modules.customers.models import Customer
from app.modules.customers.service import (
    create_customer, delete_customer, get_customer, list_customers,
    restore_customer, update_customer,
)

router = APIRouter()


class CustomerIn(BaseModel):
    name: str
    inn: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    discount_card: Optional[str] = None
    personal_discount: float = 0.0
    comment: Optional[str] = None


class CustomerPatch(BaseModel):
    name: Optional[str] = None
    inn: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    discount_card: Optional[str] = None
    personal_discount: Optional[float] = None
    comment: Optional[str] = None

# Примечание контроллера: импорт get_customer в списке используется в api_customer
# (реализовано через db.get(Customer, ...)). Не удаляй импорт, сохрани список как в плане.


@router.get("/customers")
def api_customers(
    search: Optional[str] = Query(None),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    ensure_permissions(user, Permission.customers_manage)
    items, total = list_customers(db, search=search or None, include_deleted=include_deleted,
                                  page=page, page_size=page_size)
    return {"items": [_customer_out(c) for c in items], "total": total, "page": page, "page_size": page_size}


@router.post("/customers", status_code=201)
def api_create_customer(payload: CustomerIn, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.customers_manage)
    try:
        customer = create_customer(
            db, name=payload.name, inn=payload.inn, phone=payload.phone,
            email=payload.email, discount_card=payload.discount_card,
            personal_discount=payload.personal_discount, comment=payload.comment, user=user,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _customer_out(customer)


@router.get("/customers/{customer_id}")
def api_customer(customer_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.customers_manage)
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return _customer_out(customer)


@router.patch("/customers/{customer_id}")
def api_patch_customer(customer_id: int, payload: CustomerPatch,
                       user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.customers_manage)
    if db.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    try:
        customer = update_customer(db, customer_id, user=user,
                                   **payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _customer_out(customer)


@router.delete("/customers/{customer_id}", status_code=204)
def api_delete_customer(customer_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.customers_manage)
    if db.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    try:
        delete_customer(db, customer_id, user=user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/customers/{customer_id}/restore")
def api_restore_customer(customer_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    ensure_permissions(user, Permission.customers_manage)
    if db.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    try:
        restore_customer(db, customer_id, user=user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _customer_out(db.get(Customer, customer_id))


def _customer_out(c: Customer) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "inn": c.inn,
        "phone": c.phone,
        "email": c.email,
        "discount_card": c.discount_card,
        "personal_discount": c.personal_discount,
        "comment": c.comment,
        "is_deleted": c.is_deleted,
    }