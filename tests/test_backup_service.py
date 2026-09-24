import os
import re
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name
from app.modules.backup.models import BackupLog, BackupSettings
from app.modules.backup.service import (
    EMAIL_RE,
    archive_name,
    create_backup,
    delete_expired,
    ensure_default_backup_settings,
    find_binary,
    get_backup_settings,
    is_compatible,
    list_archives,
    parse_archive_name,
    parse_binary_version,
    pg_dump_command,
    pg_dump_health,
    pg_env,
    pg_restore_command,
    unique_path,
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


def test_pg_env_preserves_socket_host(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.database_url",
        "postgresql:///smartsklad?host=/var/run/postgresql",
    )
    env = pg_env()
    assert env["PGHOST"] == "/var/run/postgresql"


def test_pg_env_does_not_default_missing_host(monkeypatch):
    monkeypatch.setattr("app.config.settings.database_url", "postgresql:///smartsklad")
    env = pg_env()
    assert "PGHOST" not in env


def test_dump_command_has_no_password(tmp_path):
    password = pg_env()["PGPASSWORD"]
    database = pg_env()["PGDATABASE"]
    command = pg_dump_command("pg_dump", tmp_path / "a.dump")
    joined = " ".join(command)
    assert command[0] == "pg_dump"
    assert "--format=custom" in command
    assert "--no-password" in command
    assert "--dbname" not in command
    assert "-d" not in command
    assert database not in command
    assert password not in command
    for argument in command:
        assert password not in argument or argument.endswith(".dump")
    assert "postgresql" not in joined


def test_restore_command_cleans_and_keeps_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.database_url",
        "postgresql://smartsklad:test-secret@localhost:5432/smartsklad",
    )
    prepared = pg_restore_command("pg_restore", tmp_path / "a.dump")
    command = prepared["command"]
    password = prepared["env"]["PGPASSWORD"]
    assert command[0] == "pg_restore"
    assert "--clean" in command
    assert "--if-exists" in command
    assert "--create" not in command
    assert password not in command
    for argument in command:
        assert password not in argument or argument.endswith(".dump")
    assert prepared["env"]["PGPASSWORD"] == "test-secret"


def test_find_binary_prefers_configured_path(tmp_path):
    binary = tmp_path / "pg_dump.exe"
    binary.write_text("stub", encoding="utf-8")
    assert find_binary("pg_dump", str(binary)) == str(binary)


def test_find_binary_returns_none_for_missing_configured_path(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "C:/should-not-be-used/pg_dump.exe")
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
    assert health["restore_found"] is False
    assert health["restore_path"] is None
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
    assert health["restore_found"] is True
    assert health["restore_path"] == str(binary)


def test_health_rejects_file_as_backup_directory(db_session, tmp_path, monkeypatch):
    row = ensure_default_backup_settings(db_session)
    local_path = tmp_path / "backups"
    local_path.write_text("not a directory", encoding="utf-8")
    row.local_path = str(local_path)
    db_session.commit()
    monkeypatch.setattr("shutil.which", lambda name: None)
    health = pg_dump_health(db_session, row)
    assert health["path_writable"] is False


def test_health_finds_sibling_restore_for_configured_dump(db_session, tmp_path, monkeypatch):
    import app.modules.backup.service as service

    row = ensure_default_backup_settings(db_session)
    row.local_path = str(tmp_path)
    dump = tmp_path / "pg_dump.exe"
    restore = tmp_path / "pg_restore.exe"
    dump.write_text("stub", encoding="utf-8")
    restore.write_text("stub", encoding="utf-8")
    row.pg_dump_path = str(dump)
    db_session.commit()
    monkeypatch.setattr("shutil.which", lambda name: "C:/should-not-be-used/pg_restore.exe")
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, "pg_dump (PostgreSQL) 18.6", ""),
    )
    health = service.pg_dump_health(db_session, row)
    assert health["dump_found"] is True
    assert health["dump_path"] == str(dump)
    assert health["restore_found"] is True
    assert health["restore_path"] == str(restore)


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


def test_health_survives_parser_failure(db_session, tmp_path, monkeypatch):
    import app.modules.backup.service as service

    row = ensure_default_backup_settings(db_session)
    row.local_path = str(tmp_path)
    row.pg_dump_path = str(tmp_path / "pg_dump.exe")
    db_session.commit()
    binary = tmp_path / "pg_dump.exe"
    binary.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, "weird", ""),
    )

    def explode(text):
        raise RuntimeError("парсер сломался")

    monkeypatch.setattr(service, "parse_binary_version", explode)
    health = service.pg_dump_health(db_session, row)
    assert health["dump_major"] is None


def test_email_regex_rejects_garbage():
    assert EMAIL_RE.match("boss@example.com")
    assert not EMAIL_RE.match("boss")
    assert not EMAIL_RE.match("")
    assert not EMAIL_RE.match("a@b")


def test_archive_name_format():
    assert archive_name(datetime(2026, 9, 25, 3, 4, 5)) == "smartsklad-20260925-030405.dump"


def test_parse_archive_name_roundtrip():
    stamp = datetime(2026, 9, 25, 3, 4, 5)
    assert parse_archive_name(archive_name(stamp)) == stamp


def test_parse_archive_name_with_collision_suffix():
    assert parse_archive_name("smartsklad-20260925-030405-2.dump") == datetime(2026, 9, 25, 3, 4, 5)


def test_parse_archive_name_rejects_foreign_files():
    assert parse_archive_name("readme.txt") is None
    assert parse_archive_name("smartsklad-20261325-030405.dump") is None
    assert parse_archive_name("smartsklad-20260925-030405.dump.part") is None


def test_unique_path_avoids_overwrite(tmp_path):
    stamp = datetime(2026, 9, 25, 3, 4, 5)
    stem = "smartsklad-20260925-030405"
    first = unique_path(tmp_path, stem, ".dump")
    first.write_text("a", encoding="utf-8")
    second = unique_path(tmp_path, stem, ".dump")
    assert second != first
    assert second.name == "smartsklad-20260925-030405-1.dump"
    assert first.read_text(encoding="utf-8") == "a"
    assert parse_archive_name(second.name) == stamp


def test_list_archives_ignores_foreign_files(tmp_path):
    older = tmp_path / archive_name(datetime(2026, 9, 25, 3, 4, 4))
    newer = tmp_path / archive_name(datetime(2026, 9, 25, 3, 4, 5))
    older.write_bytes(b"old")
    newer.write_bytes(b"new archive")
    (tmp_path / "notes.txt").write_text("b", encoding="utf-8")
    (tmp_path / "smartsklad-20260925-030405.dump.part").write_text("c", encoding="utf-8")

    class Row:
        local_path = str(tmp_path)
        pg_dump_path = None

    archives = list_archives(Row())
    assert [a["name"] for a in archives] == [newer.name, older.name]
    assert archives[0] == {
        "name": newer.name,
        "size_bytes": len(b"new archive"),
        "path": str(newer),
    }
    assert archives[1] == {
        "name": older.name,
        "size_bytes": len(b"old"),
        "path": str(older),
    }


def test_delete_expired_removes_only_old(tmp_path):
    now = datetime(2026, 9, 25, 12, 0)
    fresh = tmp_path / archive_name(now - timedelta(days=1))
    old = tmp_path / archive_name(now - timedelta(days=40))
    fresh.write_text("a", encoding="utf-8")
    old.write_text("b", encoding="utf-8")

    class Row:
        local_path = str(tmp_path)
        pg_dump_path = None
        retention_days = 30

    assert delete_expired(Row(), now) == 1
    assert fresh.exists()
    assert not old.exists()


def _stub_dump(payload=b"archive"):
    def fake_run(command, **kwargs):
        target = Path(command[command.index("--file") + 1])
        target.write_bytes(payload)
        return subprocess.CompletedProcess(command, 0, "", "")
    return fake_run


def test_create_backup_writes_archive_and_log(db_session, tmp_path, monkeypatch):
    from app.modules.backup.service import get_backup_settings
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    monkeypatch.setattr("app.modules.backup.service.find_binary", lambda name, configured=None: name)
    monkeypatch.setattr("app.modules.backup.service.subprocess.run", _stub_dump())
    result = create_backup(db_session, now=datetime(2026, 9, 25, 3, 0))
    assert result["status"] == "success"
    assert result["filename"] == "smartsklad-20260925-030000.dump"
    assert (tmp_path / result["filename"]).exists()
    assert not list(tmp_path.glob("*.part"))
    assert row.last_backup_at == datetime(2026, 9, 25, 3, 0)
    assert row.last_status == "success"
    entry = db_session.query(BackupLog).one()
    assert entry.kind == "manual"
    assert entry.status == "success"
    assert entry.size_bytes == len(b"archive")


def test_create_backup_records_failure_and_removes_partial(db_session, tmp_path, monkeypatch):
    from app.modules.backup.service import get_backup_settings
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    monkeypatch.setattr("app.modules.backup.service.find_binary", lambda name, configured=None: name)

    def failing(command, **kwargs):
        target = Path(command[command.index("--file") + 1])
        target.write_text("partial", encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, "", "pg_dump: ошибка подключения")

    monkeypatch.setattr("app.modules.backup.service.subprocess.run", failing)
    result = create_backup(db_session, now=datetime(2026, 9, 25, 3, 0))
    assert result["status"] == "failed"
    assert "ошибка подключения" in result["error"]
    assert list(tmp_path.iterdir()) == []
    assert row.last_backup_at is None
    assert row.last_status != "success"
    entry = db_session.query(BackupLog).one()
    assert entry.status == "failed"


def test_create_backup_without_binary_is_logged_not_raised(db_session, tmp_path, monkeypatch):
    from app.modules.backup.service import get_backup_settings
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    monkeypatch.setattr("app.modules.backup.service.find_binary", lambda name, configured=None: None)
    result = create_backup(db_session, now=datetime(2026, 9, 25, 3, 0))
    assert result["status"] == "failed"
    assert "pg_dump" in result["error"]


def test_create_backup_blocked_directory_is_logged(db_session, tmp_path):
    row = get_backup_settings(db_session)
    blocker = tmp_path / "blocker"
    blocker.write_text("file, not a folder", encoding="utf-8")
    row.local_path = str(blocker / "backups")
    db_session.commit()
    result = create_backup(db_session, now=datetime(2026, 9, 25, 3, 0))
    assert result["status"] == "failed"
    assert db_session.query(BackupLog).count() == 1
    assert db_session.query(BackupLog).one().status == "failed"


def test_update_backup_settings_validates_before_mutating(db_session):
    from app.modules.backup.service import update_backup_settings
    row = get_backup_settings(db_session)
    row.retention_days = 11
    db_session.commit()
    with pytest.raises(ValueError):
        update_backup_settings(db_session, enabled=True, schedule_time="04:30",
                               check_interval_minutes=0, retention_days=30, local_path="b")
    assert get_backup_settings(db_session).retention_days == 11


def test_update_backup_settings_rejects_bad_time(db_session):
    from app.modules.backup.service import update_backup_settings
    with pytest.raises(ValueError):
        update_backup_settings(db_session, enabled=True, schedule_time="25:99",
                               check_interval_minutes=15, retention_days=30, local_path="b")


def test_update_backup_settings_normalises_empty_path(db_session):
    from app.modules.backup.service import update_backup_settings
    update_backup_settings(db_session, enabled=True, schedule_time="04:30",
                           check_interval_minutes=20, retention_days=15,
                           local_path="backups-x", pg_dump_path="")
    row = get_backup_settings(db_session)
    assert row.pg_dump_path is None
    assert row.local_path == "backups-x"
    assert row.check_interval_minutes == 20
    assert row.schedule_time.strftime("%H:%M") == "04:30"


def test_create_backup_timeout_is_logged_and_cleans_partial(db_session, tmp_path, monkeypatch):
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    monkeypatch.setattr("app.modules.backup.service.find_binary", lambda name, configured=None: name)

    def timeout(command, **kwargs):
        target = Path(command[command.index("--file") + 1])
        target.write_text("half written", encoding="utf-8")
        raise subprocess.TimeoutExpired(command, 600)

    monkeypatch.setattr("app.modules.backup.service.subprocess.run", timeout)
    result = create_backup(db_session, now=datetime(2026, 9, 25, 3, 0))
    assert result["status"] == "failed"
    assert list(tmp_path.iterdir()) == []
    assert db_session.query(BackupLog).one().status == "failed"
