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


def test_running_blocks_and_back_to_idle():
    set_running()
    assert maintenance_state()["mode"] == "running"
    set_idle()
    assert maintenance_state()["mode"] == "idle"


def test_running_mode_blocks_pages_and_recovers(client, db_session):
    _admin(client, db_session)
    set_running()
    try:
        blocked = client.get("/products")
        assert blocked.status_code == 503
        allowed = client.get("/api/backup/status")
        assert allowed.status_code != 503
    finally:
        set_idle()
    assert client.get("/products").status_code == 200


def test_restore_command_is_called(db_session, tmp_path, monkeypatch):
    from app.modules.backup.service import get_backup_settings, run_restore
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030000.dump"
    archive.write_bytes(b"archive")
    calls = []
    monkeypatch.setattr("app.modules.backup.service.find_binary", lambda name, configured=None: name)
    monkeypatch.setattr("app.modules.backup.service.subprocess.run",
                        lambda command, **kwargs: calls.append(command) or
                        __import__("subprocess").CompletedProcess(command, 0, "", ""))
    result = run_restore(db_session, "smartsklad-20260925-030000.dump")
    assert result["status"] == "success"
    assert "--clean" in calls[0]
    assert "--if-exists" in calls[0]
    assert "--create" not in calls[0]


def test_restore_unknown_file_is_not_found(db_session, tmp_path, monkeypatch):
    from app.modules.backup.service import get_backup_settings, run_restore
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    with pytest.raises(FileNotFoundError):
        run_restore(db_session, "smartsklad-20200101-000000.dump")


def test_perform_restore_runs_and_returns_to_idle(monkeypatch):
    import app.modules.backup.service as service
    from app.modules.backup.maintenance import maintenance_state

    class FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(service, "SessionLocal", FakeSession)
    monkeypatch.setattr(service, "engine", type("E", (), {"dispose": lambda self: None})())
    seen = {}
    monkeypatch.setattr(service, "run_restore",
                        lambda db, filename, user_id=None: seen.update(
                            {"filename": filename, "user_id": user_id}) or {"status": "success"})
    result = service.perform_restore("smartsklad-20260925-030000.dump", user_id=7)
    assert result["status"] == "success"
    assert seen == {"filename": "smartsklad-20260925-030000.dump", "user_id": 7}
    assert maintenance_state()["mode"] == "idle"


def test_perform_restore_returns_to_idle_even_on_failure(monkeypatch):
    import app.modules.backup.service as service
    from app.modules.backup.maintenance import maintenance_state

    class FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(service, "SessionLocal", FakeSession)
    monkeypatch.setattr(service, "engine", type("E", (), {"dispose": lambda self: None})())

    def boom(*args, **kwargs):
        raise RuntimeError("pg_restore упал")

    monkeypatch.setattr(service, "run_restore", boom)
    with pytest.raises(RuntimeError):
        service.perform_restore("smartsklad-20260925-030000.dump")
    assert maintenance_state()["mode"] == "idle"
