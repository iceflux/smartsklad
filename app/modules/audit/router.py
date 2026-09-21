import json
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.audit.service import list_audit_logs
from app.modules.auth.dependencies import (
    get_page_user, page_context, user_has_permission,
)
from app.modules.auth.models import User
from app.modules.auth.permissions import Permission

router = APIRouter()


def _fmt_value(value: str | None) -> str:
    if value is None:
        return "—"
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    if isinstance(parsed, (dict, list)):
        s = json.dumps(parsed, ensure_ascii=False)
        return s[:200] + "…" if len(s) > 200 else s
    return json.dumps(parsed, ensure_ascii=False)


def _parse_dt(raw: str | None) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    dt = datetime.fromisoformat(raw.strip())
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/audit")
def audit_page(
    request: Request,
    user: Annotated[User | None, Depends(get_page_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    user_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    from_: str | None = None,
    to_: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not user_has_permission(user, Permission.audit_view):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    try:
        f_from = _parse_dt(from_)
        f_to = _parse_dt(to_)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректная дата фильтра")
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    rows, total = list_audit_logs(
        db, user_id=user_id, action=action, entity_type=entity_type,
        from_=f_from, to_=f_to, page=page, page_size=page_size,
    )
    users = db.query(User).order_by(User.username).all()
    usernames = {u.id: u.username for u in users}
    logs = [
        {
            "id": l.id,
            "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else "",
            "username": usernames.get(l.user_id) or "система",
            "action": l.action,
            "entity": f"{l.entity_type} #{l.entity_id}" if l.entity_id is not None else l.entity_type,
            "old_value": _fmt_value(l.old_value),
            "new_value": _fmt_value(l.new_value),
            "ip": l.ip or "—",
        }
        for l in rows
    ]
    pages = max(1, (total + page_size - 1) // page_size)
    base_qs = urlencode({k: v for k, v in {
        "user_id": user_id, "action": action, "entity_type": entity_type,
        "from_": from_, "to_": to_, "page_size": page_size,
    }.items() if v not in (None, "")})
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "audit.html", {
        "title": "Журнал аудита", "user": user, "logs": logs, "total": total,
        "page": page, "pages": pages, "page_size": page_size, "base_qs": base_qs,
        "users_choices": [{"id": u.id, "username": u.username} for u in users],
        "filters": {
            "user_id": user_id, "action": action or "", "entity_type": entity_type or "",
            "from_": from_ or "", "to_": to_ or "",
        },
        **page_context(user),
    })