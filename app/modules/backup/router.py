import re
from datetime import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.permissions import Permission
from app.modules.backup.maintenance import begin_countdown, maintenance_state
from app.modules.backup.report import read_report_settings, write_report_settings
from app.modules.backup.service import (
    create_backup,
    get_backup_settings,
    list_archives,
    pg_dump_health,
)
from app.modules.audit.service import log_action

router = APIRouter()

TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class SettingsIn(BaseModel):
    enabled: bool
    schedule_time: str
    check_interval_minutes: int
    retention_days: int
    local_path: str
    pg_dump_path: str | None = None


class ReportIn(BaseModel):
    enabled: bool
    period: str
    send_time: str
    recipient_ids: list[int] = []


@router.get("/backup/status")
def backup_status(db=Depends(get_db),
                  user=Depends(require_permissions(Permission.backup_manage))):
    row = get_backup_settings(db)
    return {
        "scheduler": {
            "last_check_at": row.last_check_at,
            "last_backup_at": row.last_backup_at,
            "last_status": row.last_status,
            "last_error": row.last_error,
            "last_report_at": row.last_report_at,
            "last_report_sent": row.last_report_sent,
            "last_report_skipped": row.last_report_skipped,
            "check_interval_minutes": row.check_interval_minutes,
            "schedule_time": row.schedule_time.strftime("%H:%M"),
        },
        "health": pg_dump_health(db, row),
        "archives": list_archives(row),
        "maintenance": maintenance_state(),
    }


@router.get("/backup/archives")
def backup_archives(db=Depends(get_db),
                    user=Depends(require_permissions(Permission.backup_manage))):
    return list_archives(get_backup_settings(db))


@router.post("/backup/run")
def backup_run(db=Depends(get_db),
               user=Depends(require_permissions(Permission.backup_manage))):
    try:
        return create_backup(db, user_id=user.id, kind="manual")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/backup/settings")
def backup_settings_get(db=Depends(get_db),
                        user=Depends(require_permissions(Permission.backup_manage))):
    row = get_backup_settings(db)
    return {
        "enabled": row.enabled,
        "schedule_time": row.schedule_time.strftime("%H:%M"),
        "check_interval_minutes": row.check_interval_minutes,
        "retention_days": row.retention_days,
        "local_path": row.local_path,
        "pg_dump_path": row.pg_dump_path,
    }


@router.put("/backup/settings")
def backup_settings_put(payload: SettingsIn, db=Depends(get_db),
                        user=Depends(require_permissions(Permission.backup_manage))):
    if not TIME_RE.fullmatch(payload.schedule_time or ""):
        raise HTTPException(status_code=422, detail="время в формате ЧЧ:ММ")
    schedule_time = time.fromisoformat(payload.schedule_time)
    if not 1 <= payload.check_interval_minutes <= 1440:
        raise HTTPException(status_code=422, detail="период проверки от 1 до 1440 минут")
    if not 1 <= payload.retention_days <= 365:
        raise HTTPException(status_code=422, detail="срок хранения от 1 до 365 дней")
    local_path = (payload.local_path or "").strip()
    if not local_path:
        raise HTTPException(status_code=422, detail="папка для копий обязательна")
    row = get_backup_settings(db)
    before = {"enabled": row.enabled, "schedule_time": row.schedule_time.strftime("%H:%M"),
              "check_interval_minutes": row.check_interval_minutes,
              "retention_days": row.retention_days, "local_path": row.local_path}
    row.enabled = payload.enabled
    row.schedule_time = schedule_time
    row.check_interval_minutes = payload.check_interval_minutes
    row.retention_days = payload.retention_days
    row.local_path = local_path
    row.pg_dump_path = (payload.pg_dump_path or "").strip() or None
    db.commit()
    log_action(db, action="backup.settings", entity_type="settings", entity_id=row.id,
               old_value=str(before), new_value=f"{payload.schedule_time} {local_path}",
               user_id=user.id)
    return backup_settings_get(db=db, user=user)


@router.get("/backup/report")
def backup_report_get(db=Depends(get_db),
                      user=Depends(require_permissions(Permission.backup_manage))):
    return read_report_settings(db)


@router.put("/backup/report")
def backup_report_put(payload: ReportIn, db=Depends(get_db),
                      user=Depends(require_permissions(Permission.backup_manage))):
    try:
        return write_report_settings(
            db, enabled=payload.enabled, period=payload.period,
            send_time=payload.send_time, recipient_ids=payload.recipient_ids,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
