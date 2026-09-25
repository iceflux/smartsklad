import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.backup.maintenance import (
    begin_countdown,
    cancel_countdown,
    maintenance_state,
    set_idle,
    set_running,
)
from app.modules.backup.models import BackupLog

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)
    return u


def test_starts_idle():
    assert maintenance_state()["mode"] == "idle"


def test_countdown_is_visible():
    begin_countdown(120)
    state = maintenance_state()
    assert state["mode"] == "countdown"
    assert state["seconds_left"] > 0
    assert state["starts_at"] is not None
    cancel_countdown()


def test_begin_countdown_refuses_to_overwrite_countdown():
    begin_countdown(120)
    starts_at = maintenance_state()["starts_at"]
    with pytest.raises(ValueError):
        begin_countdown(120)
    assert maintenance_state()["mode"] == "countdown"
    assert maintenance_state()["starts_at"] == starts_at
    cancel_countdown()


def test_countdown_expires_to_zero():
    from app.modules.backup import maintenance

    begin_countdown(10)
    maintenance.STATE["starts_at"] -= 10
    assert maintenance_state()["mode"] == "countdown"
    assert maintenance_state()["seconds_left"] == 0
    set_idle()


@pytest.mark.parametrize("seconds", [5, 4000])
def test_begin_countdown_rejects_out_of_range(seconds):
    with pytest.raises(ValueError):
        begin_countdown(seconds)


def test_running_blocks_and_back_to_idle():
    set_running()
    assert maintenance_state()["mode"] == "running"
    set_idle()
    assert maintenance_state()["mode"] == "idle"


def test_cancel_countdown_does_not_stop_running_restore():
    set_running()
    cancel_countdown()
    assert maintenance_state()["mode"] == "running"
    set_idle()


def test_running_mode_blocks_pages_and_recovers(client, db_session):
    _admin(client, db_session)
    set_running()
    try:
        blocked = client.get("/products")
        assert blocked.status_code == 503
        boundary_blocked = client.get("/api/backup/status-anything")
        assert boundary_blocked.status_code == 503
        allowed = client.get("/api/backup/status")
        assert allowed.status_code != 503
        static = client.get("/static/missing.css")
        assert static.status_code != 503
    finally:
        set_idle()
    assert client.get("/products").status_code == 200


def test_arm_restore_cancellation_returns_to_idle():
    from app.modules.backup import maintenance

    async def scenario():
        task = maintenance.arm_restore("smartsklad-20260925-030000.dump", 3600)
        await asyncio.sleep(0)
        assert maintenance_state()["mode"] == "countdown"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
        assert maintenance_state()["mode"] == "idle"
        assert maintenance.STATE["restore_task"] is None
    finally:
        set_idle()


def test_cancel_countdown_prevents_restore_from_running(monkeypatch):
    import app.modules.backup.service as service
    from app.modules.backup import maintenance

    calls = []
    original_sleep = asyncio.sleep

    async def blocked_sleep(seconds):
        await asyncio.Event().wait()

    def restore(filename, *, user_id=None):
        calls.append(filename)
        return {"status": "success"}

    monkeypatch.setattr(maintenance.asyncio, "sleep", blocked_sleep)
    monkeypatch.setattr(service, "perform_restore", restore)

    async def scenario():
        task = maintenance.arm_restore("smartsklad-20260925-030000.dump", 3600)
        assert isinstance(task, asyncio.Task)
        await original_sleep(0)
        cancel_countdown()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
    finally:
        set_idle()

    assert calls == []
    assert maintenance.STATE["restore_task"] is None


def test_task_cancellation_after_running_leaves_mode_running(monkeypatch):
    import app.modules.backup.service as service
    from app.modules.backup import maintenance

    calls = []

    async def immediate_sleep(seconds):
        return None

    async def scenario():
        started = asyncio.Event()

        async def blocked_to_thread(function, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()
            return function(*args, **kwargs)

        def restore(filename, *, user_id=None):
            calls.append(filename)
            return {"status": "success"}

        monkeypatch.setattr(maintenance.asyncio, "sleep", immediate_sleep)
        monkeypatch.setattr(maintenance.asyncio, "to_thread", blocked_to_thread)
        monkeypatch.setattr(service, "perform_restore", restore)
        task = maintenance.arm_restore("smartsklad-20260925-030000.dump", 3600)
        await asyncio.wait_for(started.wait(), 1)
        set_running()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert maintenance_state()["mode"] == "running"
        assert maintenance.STATE["restore_task"] is None

    try:
        asyncio.run(scenario())
    finally:
        set_idle()

    assert calls == []


def test_immediate_rearming_after_cancel_preserves_new_countdown(monkeypatch):
    import app.modules.backup.service as service
    from app.modules.backup import maintenance

    calls = []
    original_sleep = asyncio.sleep

    async def scenario():
        release = asyncio.Event()

        async def blocked_sleep(seconds):
            await release.wait()

        def restore(filename, *, user_id=None):
            calls.append(filename)
            return {"status": "success"}

        monkeypatch.setattr(maintenance.asyncio, "sleep", blocked_sleep)
        monkeypatch.setattr(service, "perform_restore", restore)
        first = maintenance.arm_restore("smartsklad-20260925-030000.dump", 3600)
        await original_sleep(0)
        cancel_countdown()
        second = maintenance.arm_restore("smartsklad-20260925-030000.dump", 3600)
        try:
            await original_sleep(0)
            assert maintenance_state()["mode"] == "countdown"
            assert second.done() is False
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            await second
        finally:
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        set_idle()

    assert calls == ["smartsklad-20260925-030000.dump"]


@pytest.mark.parametrize(
    "filename",
    [
        "../smartsklad-20260925-030000.dump",
        "sub/smartsklad-20260925-030000.dump",
        "..\\smartsklad-20260925-030000.dump",
    ],
)
def test_restore_rejects_path_traversal(db_session, filename):
    from app.modules.backup.service import run_restore

    with pytest.raises(ValueError):
        run_restore(db_session, filename)


def test_restore_command_is_called(db_session, tmp_path, monkeypatch):
    import app.modules.backup.service as service

    row = service.get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030000.dump"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(
        "app.config.settings.database_url",
        "postgresql://smartsklad:test-secret@localhost:5432/smartsklad",
    )
    password = service.pg_env()["PGPASSWORD"]
    calls = []
    monkeypatch.setattr(service, "find_restore_binary", lambda configured=None: "pg_restore")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    result = service.run_restore(db_session, "smartsklad-20260925-030000.dump")
    assert result["status"] == "success"
    command, kwargs = calls[0]
    assert "--clean" in command
    assert "--if-exists" in command
    assert "--create" not in command
    assert kwargs["env"]["PGPASSWORD"] == password
    assert all(password not in argument for argument in command)


def test_restore_uses_sibling_binary_for_configured_dump(db_session, tmp_path, monkeypatch):
    import app.modules.backup.service as service

    row = service.get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    dump = tmp_path / "pg_dump.exe"
    restore = tmp_path / "pg_restore.exe"
    dump.write_bytes(b"dump")
    restore.write_bytes(b"restore")
    row.pg_dump_path = str(dump)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030000.dump"
    archive.write_bytes(b"archive")
    calls = []
    monkeypatch.setattr("shutil.which", lambda name: None)

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    result = service.run_restore(db_session, archive.name)

    assert result["status"] == "success"
    assert calls == [[str(restore), "--clean", "--if-exists", "--no-password",
                      "--dbname", "smartsklad", str(archive)]]


def test_restore_releases_settings_transaction_before_subprocess(db_session, tmp_path, monkeypatch):
    import app.modules.backup.service as service

    row = service.get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030000.dump"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(service, "find_restore_binary", lambda configured=None: "pg_restore")

    def fake_run(command, **kwargs):
        assert not db_session.in_transaction()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    result = service.run_restore(db_session, "smartsklad-20260925-030000.dump")
    assert result["status"] == "success"


def test_restore_unknown_file_is_not_found(db_session, tmp_path, monkeypatch):
    from app.modules.backup.service import get_backup_settings, run_restore
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    with pytest.raises(FileNotFoundError):
        run_restore(db_session, "smartsklad-20200101-000000.dump")


def test_missing_restore_is_logged_and_audited(db_session, tmp_path, monkeypatch):
    import app.modules.backup.service as service
    from app.modules.audit.models import AuditLog

    row = service.get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030000.dump"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(service, "find_restore_binary", lambda configured=None: None)

    result = service.run_restore(db_session, archive.name)

    assert result["status"] == "failed"
    entry = db_session.query(BackupLog).one()
    assert entry.status == "failed"
    assert entry.error == "pg_restore не найден"
    audit = db_session.query(AuditLog).filter(AuditLog.action == "backup.restore").one()
    assert audit.entity_id == entry.id


def test_perform_restore_runs_and_returns_to_idle(monkeypatch):
    import app.modules.backup.service as service
    from app.modules.backup.maintenance import maintenance_state

    events = []

    class FakeSession:
        def close(self):
            events.append("close")

    class FakeEngine:
        def dispose(self):
            events.append("dispose")

    def sleep(seconds):
        events.append(("sleep", seconds, maintenance_state()["mode"]))

    def restore(db, filename, user_id=None):
        events.append("restore")
        return {"status": "success"}

    monkeypatch.setattr(service, "SessionLocal", FakeSession)
    monkeypatch.setattr(service, "engine", FakeEngine())
    monkeypatch.setattr(service.clock, "sleep", sleep)
    monkeypatch.setattr(service, "run_restore", restore)
    result = service.perform_restore("smartsklad-20260925-030000.dump", user_id=7)
    assert result["status"] == "success"
    assert events == [("sleep", 2, "running"), "dispose", "restore", "close"]
    assert maintenance_state()["mode"] == "idle"


def test_perform_restore_returns_to_idle_even_on_failure(db_session, tmp_path, monkeypatch):
    import app.modules.backup.service as service

    row = service.get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030000.dump"
    archive.write_bytes(b"archive")

    class TrackingSession:
        def __init__(self):
            self.closed = False

        def __getattr__(self, name):
            return getattr(db_session, name)

        def close(self):
            self.closed = True
            db_session.close()

    session = TrackingSession()
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(service, "engine", type("E", (), {"dispose": lambda self: None})())
    monkeypatch.setattr(service.clock, "sleep", lambda seconds: None)
    monkeypatch.setattr(service, "find_restore_binary", lambda configured=None: "pg_restore")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 3600)

    monkeypatch.setattr(service.subprocess, "run", timeout)
    calls = []
    original_run_restore = service.run_restore

    def tracked_run_restore(*args, **kwargs):
        calls.append((args, kwargs))
        return original_run_restore(*args, **kwargs)

    monkeypatch.setattr(service, "run_restore", tracked_run_restore)
    result = service.perform_restore("smartsklad-20260925-030000.dump")
    assert result["status"] == "failed"
    assert len(calls) == 1
    entry = db_session.query(BackupLog).filter_by(
        kind="restore", filename="smartsklad-20260925-030000.dump"
    ).one()
    assert entry.status == "failed"
    assert entry.error
    from app.modules.audit.models import AuditLog
    audit = db_session.query(AuditLog).filter(AuditLog.action == "backup.restore").one()
    assert audit.entity_id == entry.id
    assert session.closed is True
    assert maintenance_state()["mode"] == "idle"
