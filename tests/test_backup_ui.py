from urllib.parse import quote, unquote_plus, urlencode

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)
    return u


def test_backups_redirects_anonymous(client, db_session):
    r = client.get("/backups", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


def test_backups_403_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "plain", PASSWORD).token)
    assert client.get("/backups").status_code == 403


def test_backups_renders_all_blocks(client, db_session):
    _admin(client, db_session)
    r = client.get("/backups")
    assert r.status_code == 200
    for text in ("Состояние планировщика", "Следующая копия", "Следующий отчёт",
                 "Планировщик", "Последние запуски", "Резервные копии",
                 "Настройки", "Проверка окружения", "Отчёт по email"):
        assert text in r.text


def test_backups_panel_shows_maintenance_pause_and_next_runs(client, db_session):
    from app.modules.backup.maintenance import begin_countdown, set_idle

    _admin(client, db_session)
    begin_countdown(120)
    try:
        response = client.get("/backups")
    finally:
        set_idle()
    assert response.status_code == 200
    assert "Плановые копирование и отчёт пропускаются" in response.text
    assert "Начало:" in response.text
    assert "Отменить восстановление" in response.text
    assert "Следующая копия" in response.text
    assert "Следующий отчёт" in response.text


def test_backups_settings_form_rejects_bad_values(client, db_session):
    _admin(client, db_session)
    r = client.post("/backups/settings", data={"enabled": "on", "schedule_time": "99:99",
                                               "check_interval_minutes": "0",
                                               "retention_days": "30", "local_path": "b"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "ЧЧ:ММ" in unquote_plus(r.headers["location"])


def test_backups_run_action_redirects(client, db_session, monkeypatch, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    monkeypatch.setattr("app.modules.backup.service.find_binary", lambda name, configured=None: None)
    r = client.post("/backups/run", follow_redirects=False)
    assert r.status_code == 303


def test_backups_restore_rejects_second_post_during_countdown(client, db_session, tmp_path):
    from app.modules.backup.maintenance import maintenance_state, set_idle
    from app.modules.backup.service import get_backup_settings

    set_idle()
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030405.dump"
    archive.write_bytes(b"archive")
    data = {"filename": archive.name, "delay": "3600"}
    first = client.post("/backups/restore", data=data, follow_redirects=False)
    second = client.post("/backups/restore", data=data, follow_redirects=False)
    try:
        assert first.status_code == 303
        assert second.status_code == 303
        assert "восстановление уже запланировано" in unquote_plus(second.headers["location"])
        assert maintenance_state()["mode"] == "countdown"
    finally:
        set_idle()


def test_backups_restore_cancel_resets_countdown(client, db_session):
    from app.modules.backup.maintenance import begin_countdown, maintenance_state, set_idle

    set_idle()
    _admin(client, db_session)
    begin_countdown(120)
    response = client.post("/backups/restore/cancel", follow_redirects=False)
    try:
        assert response.status_code == 303
        assert maintenance_state()["mode"] == "idle"
    finally:
        set_idle()


def test_backups_restore_requires_known_archive(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    r = client.post("/backups/restore", data={"filename": "smartsklad-20200101-000000.dump",
                                              "delay": "60"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "Архив не найден" in unquote_plus(r.headers["location"])


@pytest.mark.parametrize("delay", ["1", "99999"])
def test_backups_restore_rejects_out_of_range_delay(client, db_session, tmp_path, delay):
    from app.modules.backup.maintenance import maintenance_state, set_idle
    from app.modules.backup.service import get_backup_settings
    set_idle()
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030405.dump"
    archive.write_bytes(b"archive")
    r = client.post("/backups/restore", data={"filename": archive.name, "delay": delay},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "ожидание от 10 до 3600 секунд" in unquote_plus(r.headers["location"])
    assert maintenance_state()["mode"] == "idle"


def test_backups_restore_cancel_is_noop_while_running(client, db_session):
    from app.modules.backup.maintenance import maintenance_state, set_idle, set_running

    _admin(client, db_session)
    set_running()
    try:
        response = client.post("/backups/restore/cancel", follow_redirects=False)
        assert response.status_code == 303
        assert maintenance_state()["mode"] == "running"
    finally:
        set_idle()


    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "plain", PASSWORD).token)
    r = client.get("/products")
    assert r.status_code == 200
    assert 'href="/backups"' not in r.text


def test_nav_shows_backups_for_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/products")
    assert 'href="/backups"' in r.text


def test_page_context_has_backup_flag(db_session):
    from app.modules.auth.dependencies import page_context
    admin = User(username="boss", password_hash="x", role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    assert page_context(admin)["can_manage_backups"] is True


def test_backups_report_rejects_unknown_recipient_ids(client, db_session):
    admin = _admin(client, db_session)
    admin.email = "boss@example.com"
    db_session.commit()
    data = [
        ("enabled", "on"),
        ("period", "week"),
        ("send_time", "08:15"),
        ("recipient_ids", str(admin.id)),
        ("recipient_ids", "999"),
    ]
    r = client.post(
        "/backups/report", content=urlencode(data, doseq=True).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "получатель не найден" in unquote_plus(r.headers["location"])


def test_backups_report_malformed_recipient_id_is_handled(client, db_session):
    _admin(client, db_session)
    data = [("enabled", "on"), ("period", "week"), ("send_time", "08:15"),
            ("recipient_ids", "not-an-id")]
    response = client.post(
        "/backups/report", content=urlencode(data, doseq=True).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Ошибка:" in unquote_plus(response.headers["location"])


def test_backups_report_send_runs_report_without_changing_settings(client, db_session, monkeypatch):
    from app.modules.backup.report import read_report_settings
    from app.modules.settings.service import (
        REPORT_ENABLED_KEY,
        REPORT_PERIOD_KEY,
        REPORT_RECIPIENTS_KEY,
        REPORT_TIME_KEY,
        set_setting,
    )

    admin = _admin(client, db_session)
    admin.email = "boss@example.com"
    db_session.commit()
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_PERIOD_KEY, "week")
    set_setting(db_session, REPORT_TIME_KEY, "08:15")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, f"[{admin.id}]")
    before = read_report_settings(db_session)
    calls = []

    def fake_send(db, *, user_id=None):
        calls.append(user_id)
        return {"sent": 1, "skipped": 0, "failed": 0}

    monkeypatch.setattr("app.main.send_report", fake_send)
    response = client.post("/backups/report/send", follow_redirects=False)

    assert response.status_code == 303
    assert calls == [admin.id]
    assert "Отправлено: 1" in unquote_plus(response.headers["location"])
    assert "пропущено: 0" in unquote_plus(response.headers["location"])
    assert "ошибок: 0" in unquote_plus(response.headers["location"])
    assert read_report_settings(db_session) == before


def test_backups_download_returns_archive(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    archive = tmp_path / "smartsklad-20260925-030405.dump"
    archive.write_bytes(b"archive")
    r = client.get(f"/backups/download/{archive.name}")
    assert r.status_code == 200
    assert r.content == b"archive"


def test_backups_download_rejects_path_traversal(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path / "backups")
    db_session.commit()
    secret = tmp_path / "secret.dump"
    secret.write_bytes(b"secret")
    r = client.get("/backups/download/..%2Fsecret.dump")
    assert r.status_code == 404
    assert b"secret" not in r.content


def test_backups_download_rejects_absolute_path(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path / "backups")
    db_session.commit()
    secret = tmp_path / "secret.dump"
    secret.write_bytes(b"secret")
    filename = quote(str(secret.resolve()), safe="")
    r = client.get(f"/backups/download/{filename}")
    assert r.status_code == 404
    assert b"secret" not in r.content


def test_backups_download_rejects_windows_path_traversal(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path / "backups")
    db_session.commit()
    secret = tmp_path / "secret.dump"
    secret.write_bytes(b"secret")
    filename = quote(r"..\secret.dump", safe="")
    r = client.get(f"/backups/download/{filename}")
    assert r.status_code == 404
    assert b"secret" not in r.content


def test_backups_download_rejects_null_byte(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    filename = quote("smartsklad-20260925-030405.dump\x00.txt", safe="")
    r = client.get(f"/backups/download/{filename}")
    assert r.status_code == 404


def test_backups_download_rejects_leading_slash(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path / "backups")
    db_session.commit()
    r = client.get("/backups/download/%2Fsecret.dump")
    assert r.status_code == 404


def test_backups_download_rejects_valid_name_when_file_is_missing(client, db_session, tmp_path):
    from app.modules.backup.service import get_backup_settings
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    db_session.commit()
    r = client.get("/backups/download/smartsklad-20260925-030405.dump")
    assert r.status_code == 404


def test_backups_bad_restore_filename_does_not_start_countdown(client, db_session, tmp_path):
    from app.modules.backup.maintenance import maintenance_state, set_idle
    from app.modules.backup.service import get_backup_settings
    set_idle()
    _admin(client, db_session)
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path / "backups")
    db_session.commit()
    secret = tmp_path / "secret.dump"
    secret.write_bytes(b"secret")
    r = client.post("/backups/restore", data={"filename": "../secret.dump", "delay": "60"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "Архив не найден" in unquote_plus(r.headers["location"])
    assert maintenance_state()["mode"] == "idle"
