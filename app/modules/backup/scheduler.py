import asyncio
from datetime import datetime, time, timedelta

from app.db import SessionLocal
from app.modules.backup.maintenance import maintenance_state
from app.modules.backup.service import create_backup, get_backup_settings
from app.modules.settings.service import (
    REPORT_ENABLED_KEY,
    REPORT_PERIOD_KEY,
    REPORT_PERIODS,
    REPORT_TIME_KEY,
    get_setting,
)

ADVISORY_LOCK_ID = 7_240_119
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440
DEFAULT_INTERVAL_MINUTES = 15


def due_for_backup(bs, now: datetime, last_backup_at: datetime | None) -> bool:
    if not bs.enabled:
        return False
    if now.time() < bs.schedule_time:
        return False
    if last_backup_at is not None and last_backup_at.date() == now.date():
        return False
    return True


def due_for_report(enabled: bool, schedule_time: time, period: str, now: datetime,
                   last_report_scheduled_at: datetime | None) -> bool:
    if not enabled or period not in REPORT_PERIODS or now.time() < schedule_time:
        return False
    if last_report_scheduled_at is None:
        return True
    if period == "day":
        return last_report_scheduled_at.date() != now.date()
    return now - last_report_scheduled_at >= timedelta(days=7 if period == "week" else 30)


def next_backup_time(bs, now: datetime) -> datetime | None:
    if not bs.enabled:
        return None
    candidate = datetime.combine(now.date(), bs.schedule_time)
    if now < candidate or bs.last_backup_at is None or bs.last_backup_at.date() != now.date():
        return candidate if now < candidate else now
    return candidate + timedelta(days=1)


def next_report_time(enabled: bool, schedule_time: time, period: str, now: datetime,
                     last_report_scheduled_at: datetime | None) -> datetime | None:
    if not enabled:
        return None
    candidate = datetime.combine(now.date(), schedule_time)
    if now < candidate:
        return candidate
    if due_for_report(enabled, schedule_time, period, now, last_report_scheduled_at):
        return now
    if period == "day":
        return candidate + timedelta(days=1)
    return last_report_scheduled_at + timedelta(days=7 if period == "week" else 30)


def read_interval_minutes(db) -> int:
    value = get_backup_settings(db).check_interval_minutes
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, value or MIN_INTERVAL_MINUTES))


def _parse_time(raw: str | None, default: time) -> time:
    try:
        hour, minute = (raw or "").split(":")
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return default


def _try_lock(db) -> bool:
    try:
        acquired = db.connection().exec_driver_sql(
            f"SELECT pg_try_advisory_lock({ADVISORY_LOCK_ID})"
        ).scalar()
        db.commit()
        return bool(acquired)
    except Exception:
        db.rollback()
        return True


def _release_lock(db) -> None:
    try:
        db.connection().exec_driver_sql(f"SELECT pg_advisory_unlock({ADVISORY_LOCK_ID})")
        db.commit()
    except Exception:
        db.rollback()


def _record_error(db, label: str, error: Exception) -> None:
    db.rollback()
    try:
        bs = get_backup_settings(db)
        bs.last_error = f"{label}: {str(error)[:480]}"
        db.commit()
    except Exception:
        db.rollback()


def _run_job(label: str, db, job):
    try:
        return job()
    except Exception as error:
        _record_error(db, label, error)
        return None


def run_due_jobs(db, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    result = {"backup": None, "report": None}
    if maintenance_state()["mode"] != "idle" or not _try_lock(db):
        return result
    try:
        bs = get_backup_settings(db)
        if due_for_backup(bs, now, bs.last_backup_at):
            result["backup"] = _run_job("backup", db, lambda: create_backup(db, kind="scheduled", now=now))
        report_enabled = get_setting(db, REPORT_ENABLED_KEY) == "true"
        report_period = get_setting(db, REPORT_PERIOD_KEY) or "week"
        report_schedule_time = _parse_time(get_setting(db, REPORT_TIME_KEY), time(9, 0))
        if due_for_report(report_enabled, report_schedule_time, report_period, now,
                           bs.last_report_scheduled_at):
            from app.modules.backup.report import send_report

            result["report"] = _run_job("report", db, lambda: send_report(db, now=now))
            bs = get_backup_settings(db)
            bs.last_report_scheduled_at = now
            db.commit()
        bs = get_backup_settings(db)
        bs.last_check_at = now
        db.commit()
    except Exception as error:
        _record_error(db, "scheduler", error)
    finally:
        _release_lock(db)
    return result


def _next_interval() -> int:
    db = SessionLocal()
    try:
        return read_interval_minutes(db)
    finally:
        db.close()


def _run_once() -> dict:
    db = SessionLocal()
    try:
        return run_due_jobs(db)
    finally:
        db.close()


async def scheduler_loop() -> None:
    while True:
        try:
            interval = await asyncio.to_thread(_next_interval)
        except Exception:
            interval = DEFAULT_INTERVAL_MINUTES
        try:
            await asyncio.sleep(interval * 60)
            await asyncio.to_thread(_run_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
