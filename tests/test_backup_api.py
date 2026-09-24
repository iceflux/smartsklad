from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)
    return u


def test_status_requires_auth(client, db_session):
    assert client.get("/api/backup/status").status_code == 401


def test_status_forbidden_without_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "plain", PASSWORD).token)
    assert client.get("/api/backup/status").status_code == 403


def test_status_for_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/api/backup/status")
    assert r.status_code == 200
    body = r.json()
    for key in ("scheduler", "health", "archives", "maintenance"):
        assert key in body


def test_settings_roundtrip(client, db_session):
    _admin(client, db_session)
    payload = {"enabled": False, "schedule_time": "04:30",
               "check_interval_minutes": 30, "retention_days": 15, "local_path": "backups-x"}
    r = client.put("/api/backup/settings", json=payload)
    assert r.status_code == 200
    assert r.json()["check_interval_minutes"] == 30
    assert client.get("/api/backup/settings").json()["retention_days"] == 15


def test_settings_rejects_bad_interval(client, db_session):
    _admin(client, db_session)
    before = client.get("/api/backup/settings").json()
    r = client.put("/api/backup/settings", json={"enabled": True, "schedule_time": "04:30",
                                                 "check_interval_minutes": 0,
                                                 "retention_days": 15, "local_path": "b"})
    assert r.status_code == 422
    assert client.get("/api/backup/settings").json() == before


def test_settings_rejects_bad_time(client, db_session):
    _admin(client, db_session)
    r = client.put("/api/backup/settings", json={"enabled": True, "schedule_time": "25:99",
                                                 "check_interval_minutes": 15,
                                                 "retention_days": 15, "local_path": "b"})
    assert r.status_code == 422


def test_report_settings_validation(client, db_session):
    _admin(client, db_session)
    r = client.put("/api/backup/report", json={"enabled": True, "period": "year",
                                               "send_time": "09:00", "recipient_ids": []})
    assert r.status_code == 422


def test_report_settings_accepts_week(client, db_session):
    _admin(client, db_session)
    r = client.put("/api/backup/report", json={"enabled": True, "period": "week",
                                               "send_time": "08:15", "recipient_ids": []})
    assert r.status_code == 200
    assert r.json()["send_time"] == "08:15"


def test_archives_list(client, db_session):
    _admin(client, db_session)
    r = client.get("/api/backup/archives")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_run_requires_permission(client, db_session):
    u = User(username="plain", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "plain", PASSWORD).token)
    assert client.post("/api/backup/run").status_code == 403
