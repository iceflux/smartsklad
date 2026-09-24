from urllib.parse import quote, unquote_plus, urlencode

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
    for text in ("Состояние планировщика", "Последние запуски", "Резервные копии",
                 "Настройки", "Проверка окружения", "Отчёт по email"):
        assert text in r.text


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


def test_nav_hides_backups_without_permission(client, db_session):
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


def test_backups_report_accepts_multiple_recipient_ids(client, db_session):
    from app.modules.backup.report import read_report_settings
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
    db_session.expire_all()
    assert read_report_settings(db_session)["recipient_ids"] == [admin.id, 999]


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
