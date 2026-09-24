from datetime import time
from pathlib import Path

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name
from app.modules.backup.models import BackupLog, BackupSettings
from app.modules.backup.service import ensure_default_backup_settings, get_backup_settings

PASSWORD = "password123"


def test_ensure_default_backup_settings_seeds_single_row(db_session):
    first = ensure_default_backup_settings(db_session)
    second = ensure_default_backup_settings(db_session)
    assert first.id == second.id
    assert db_session.query(BackupSettings).count() == 1


def test_ensure_default_backup_settings_defaults(db_session):
    row = ensure_default_backup_settings(db_session)
    assert row.enabled is True
    assert row.schedule_time == time(3, 0)
    assert row.check_interval_minutes == 15
    assert row.retention_days == 30
    assert row.pg_dump_path is None
    assert row.local_path == str(Path(__file__).resolve().parents[1] / "backups")
    assert row.last_status is None
    assert row.last_report_sent == 0
    assert row.last_report_skipped == 0


def test_ensure_default_backup_settings_keeps_edited_values(db_session):
    row = ensure_default_backup_settings(db_session)
    row.retention_days = 7
    row.enabled = False
    db_session.commit()
    again = ensure_default_backup_settings(db_session)
    assert again.retention_days == 7
    assert again.enabled is False
    assert db_session.query(BackupSettings).count() == 1


def test_get_backup_settings_seeds_on_first_read(db_session):
    row = get_backup_settings(db_session)
    assert isinstance(row, BackupSettings)
    assert row.retention_days == 30


def test_backup_log_accepts_all_kinds(db_session):
    user = User(
        username="boss",
        password_hash="x",
        role_id=get_role_by_name(db_session, "admin").id,
    )
    db_session.add(user)
    db_session.commit()
    for kind in ("scheduled", "manual", "restore"):
        db_session.add(BackupLog(kind=kind, status="success", user_id=user.id))
    db_session.commit()
    rows = db_session.query(BackupLog).order_by(BackupLog.id).all()
    assert [row.kind for row in rows] == ["scheduled", "manual", "restore"]
    assert all(row.user_id == user.id for row in rows)
