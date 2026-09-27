from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


def log_action(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    user_id: int | None = None,
    ip: str | None = None,
    commit: bool = True,
) -> None:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        user_id=user_id,
        ip=ip,
    )
    db.add(entry)
    if commit:
        db.commit()


def list_audit_logs(
    db: Session,
    user_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    from_: datetime | None = None,
    to_: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AuditLog], int]:
    q = db.query(AuditLog)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if from_ is not None:
        q = q.filter(AuditLog.created_at >= from_)
    if to_ is not None:
        q = q.filter(AuditLog.created_at <= to_)
    total = q.count()
    rows = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return rows, total