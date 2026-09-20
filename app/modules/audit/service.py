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
    db.commit()