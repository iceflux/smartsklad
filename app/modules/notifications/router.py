from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.permissions import Permission
from app.modules.notifications.models import NotificationLog
from app.modules.notifications.service import send_document
from app.modules.orders.models import Order

router = APIRouter()


class SendIn(BaseModel):
    channel: str


@router.post("/orders/{order_id}/send")
def api_send_order(order_id: int, payload: SendIn,
                   user=Depends(require_permissions(Permission.notifications_send)), db=Depends(get_db)):
    if payload.channel not in ("email", "telegram"):
        raise HTTPException(status_code=422, detail="channel must be 'email' or 'telegram'")
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    try:
        send_document(db, order, payload.channel, user)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    row = db.query(NotificationLog).filter(NotificationLog.document_id == order_id).order_by(NotificationLog.id.desc()).first()
    return {"status": row.status if row else "sent", "log_id": row.id if row else None}


@router.get("/notifications")
def api_notifications(
    channel: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(require_permissions(Permission.notifications_view)),
    db=Depends(get_db),
):
    q = db.query(NotificationLog)
    if channel:
        q = q.filter(NotificationLog.channel == channel)
    if status:
        q = q.filter(NotificationLog.status == status)
    if date_from:
        q = q.filter(NotificationLog.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.filter(NotificationLog.created_at <= datetime.combine(date_to, time.max))
    total = q.count()
    rows = q.order_by(NotificationLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "id": r.id, "channel": r.channel, "recipient": r.recipient,
                "status": r.status, "error": r.error,
                "document_type": r.document_type, "document_id": r.document_id,
                "user_id": r.user_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }