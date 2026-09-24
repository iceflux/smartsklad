import os
import re
import subprocess
from datetime import time
from pathlib import Path

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name
from app.modules.backup.models import BackupLog, BackupSettings
from app.modules.backup.service import (
    EMAIL_RE,
    ensure_default_backup_settings,
    find_binary,
    get_backup_settings,
    is_compatible,
    parse_binary_version,
    pg_dump_command,
    pg_dump_health,
    pg_env,
    pg_restore_command,
)

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


def test_pg_env_carries_connection_parts():
    env = pg_env()
    assert env["PGHOST"] == "localhost"
    assert env["PGPORT"] == "5432"
    assert env["PGUSER"] == "smartsklad"
    assert env["PGDATABASE"] == "smartsklad"
    assert env["PGPASSWORD"] == "smartsklad"


def test_dump_command_has_no_password(tmp_path):
    command = pg_dump_command("pg_dump", tmp_path / "a.dump")
    joined = " ".join(command)
    assert command[0] == "pg_dump"
    assert "--format=custom" in command
    assert "--no-password" in command
    assert "smartsklad:smartsklad" not in joined
    assert "postgresql" not in joined


def test_restore_command_cleans_and_keeps_database(tmp_path):
    prepared = pg_restore_command("pg_restore", tmp_path / "a.dump")
    command = prepared["command"]
    assert command[0] == "pg_restore"
    assert "--clean" in command
    assert "--if-exists" in command
    assert "--create" not in command
    assert "smartsklad:smartsklad" not in " ".join(command)
    assert prepared["env"]["PGPASSWORD"] == "smartsklad"


def test_find_binary_prefers_configured_path(tmp_path):
    binary = tmp_path / "pg_dump.exe"
    binary.write_text("stub", encoding="utf-8")
    assert find_binary("pg_dump", str(binary)) == str(binary)


def test_find_binary_returns_none_for_missing_configured_path(tmp_path):
    assert find_binary("pg_dump", str(tmp_path / "nope.exe")) is None


def test_find_binary_falls_back_to_path(monkeypatch, tmp_path):
    binary = tmp_path / "pg_dump"
    binary.write_text("stub", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: str(binary) if name == "pg_dump" else None)
    assert find_binary("pg_dump") == str(binary)
    assert find_binary("pg_restore") is None


def test_parse_binary_version():
    assert parse_binary_version("pg_dump (PostgreSQL) 18.6") == 18
    assert parse_binary_version("pg_restore (PostgreSQL) 15.2 (Ubuntu)") == 15
    assert parse_binary_version("что-то неожиданное") is None


def test_is_compatible_rules():
    assert is_compatible(18, 18) is True
    assert is_compatible(18, 16) is True
    assert is_compatible(15, 18) is False
    assert is_compatible(None, 18) is None
    assert is_compatible(18, None) is None


def test_health_reports_missing_tools(db_session, tmp_path, monkeypatch):
    row = ensure_default_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    monkeypatch.setattr("shutil.which", lambda name: None)
    health = pg_dump_health(db_session, row)
    assert health["dump_found"] is False
    assert health["compatible"] is None


def test_health_reports_writable_directory(db_session, tmp_path, monkeypatch):
    row = ensure_default_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    binary = tmp_path / "pg_dump"
    binary.write_text("stub", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: str(binary))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: subprocess.CompletedProcess(
        a[0] if a else [], 0, "pg_dump (PostgreSQL) 18.6", ""))
    health = pg_dump_health(db_session, row)
    assert health["path_writable"] is True
    assert health["dump_major"] == 18


def test_health_survives_unexecutable_binary(db_session, tmp_path, monkeypatch):
    row = ensure_default_backup_settings(db_session)
    row.local_path = str(tmp_path)
    row.pg_dump_path = str(tmp_path / "broken.exe")
    db_session.commit()
    broken = tmp_path / "broken.exe"
    broken.write_text("not a program", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise OSError(193, "недопустимое приложение Win32")

    monkeypatch.setattr("subprocess.run", refuse)
    health = pg_dump_health(db_session, row)
    assert health["dump_found"] is True
    assert health["dump_major"] is None
    assert health["compatible"] is None


def test_email_regex_rejects_garbage():
    assert EMAIL_RE.match("boss@example.com")
    assert not EMAIL_RE.match("boss")
    assert not EMAIL_RE.match("")
    assert not EMAIL_RE.match("a@b")
