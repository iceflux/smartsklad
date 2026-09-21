import json

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.modules.audit.service import log_action
from app.modules.customers.models import Customer


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    return name


def _validate_inn(inn: str | None) -> str | None:
    if inn is None:
        return None
    inn = inn.strip()
    if not inn:
        return None
    if not (inn.isdigit() and len(inn) in (10, 12)):
        raise ValueError("inn must be 10 or 12 digits")
    return inn


def _validate_discount(value: float | None) -> float:
    if value is None or value < 0 or value > 99:
        raise ValueError("discount must be between 0 and 99")
    return value


def create_customer(db: Session, *, name: str, inn: str | None = None,
                    phone: str | None = None, email: str | None = None,
                    discount_card: str | None = None, personal_discount: float = 0.0,
                    comment: str | None = None, user=None) -> Customer:
    customer = Customer(
        name=_validate_name(name),
        inn=_validate_inn(inn),
        phone=phone,
        email=email,
        discount_card=discount_card,
        personal_discount=_validate_discount(personal_discount),
        comment=comment,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    log_action(db, action="customer.create", entity_type="customer", entity_id=customer.id,
               new_value=json.dumps(_customer_dict(customer), ensure_ascii=False),
               user_id=user.id if user else None)
    return customer


def update_customer(db: Session, customer_id: int, *, user=None, **fields) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ValueError("customer not found")
    old_value = json.dumps(_customer_dict(customer), ensure_ascii=False)
    if "name" in fields:
        customer.name = _validate_name(fields["name"])
    if "inn" in fields:
        customer.inn = _validate_inn(fields["inn"])
    if "personal_discount" in fields:
        customer.personal_discount = _validate_discount(fields["personal_discount"])
    for key in ("phone", "email", "discount_card", "comment"):
        if key in fields:
            setattr(customer, key, fields[key])
    db.commit()
    new_value = json.dumps(_customer_dict(customer), ensure_ascii=False)
    log_action(db, action="customer.update", entity_type="customer", entity_id=customer.id,
               old_value=old_value, new_value=new_value, user_id=user.id if user else None)
    return customer


def delete_customer(db: Session, customer_id: int, *, user=None) -> None:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ValueError("customer not found")
    customer.is_deleted = True
    db.commit()
    log_action(db, action="customer.delete", entity_type="customer", entity_id=customer_id,
               new_value=customer.name, user_id=user.id if user else None)


def restore_customer(db: Session, customer_id: int, *, user=None) -> None:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ValueError("customer not found")
    customer.is_deleted = False
    db.commit()
    log_action(db, action="customer.restore", entity_type="customer", entity_id=customer_id,
               new_value=customer.name, user_id=user.id if user else None)


def get_customer(db: Session, customer_id: int) -> Customer | None:
    return db.get(Customer, customer_id)


def list_customers(db: Session, *, search: str | None = None,
                   include_deleted: bool = False, page: int = 1,
                   page_size: int = 25) -> tuple[list[Customer], int]:
    query = db.query(Customer)
    if search:
        query = query.filter(or_(
            Customer.name.ilike(f"%{search}%"),
            Customer.phone.ilike(f"%{search}%"),
            Customer.inn.ilike(f"%{search}%"),
        ))
    if not include_deleted:
        query = query.filter(Customer.is_deleted.is_(False))
    total = query.count()
    items = query.order_by(Customer.name).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def _customer_dict(customer: Customer) -> dict:
    return {
        "name": customer.name,
        "inn": customer.inn,
        "phone": customer.phone,
        "email": customer.email,
        "discount_card": customer.discount_card,
        "personal_discount": customer.personal_discount,
        "comment": customer.comment,
    }