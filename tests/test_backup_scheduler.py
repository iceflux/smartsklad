from datetime import datetime, time, timedelta

import pytest

from app.modules.backup.maintenance import begin_countdown, set_idle
from app.modules.backup.scheduler import (
    due_for_backup,
    due_for_report,
    read_interval_minutes,
    run_due_jobs,
)
from app.modules.backup.service import get_backup_settings
from app.modules.settings.service import (
    REPORT_ENABLED_KEY,
    REPORT_PERIOD_KEY,
    REPORT_RECIPIENTS_KEY,
    REPORT_TIME_KEY,
    set_setting,
)


class Row:
    def __init__(self, enabled=True, schedule_time=time(3, 0), check_interval_minutes=15):
        self.enabled = enabled
        self.schedule_time = schedule_time
        self.check_interval_minutes = check_interval_minutes


def test_due_before_schedule_time():
    assert due_for_backup(Row(), datetime(2026, 9, 25, 2, 59), None) is False


def test_due_after_schedule_time():
    assert due_for_backup(Row(), datetime(2026, 9, 25, 3, 0), None) is True


def test_not_due_when_disabled():
    assert due_for_backup(Row(enabled=False), datetime(2026, 9, 25, 4, 0), None) is False


def test_not_due_twice_in_same_day():
    today = datetime(2026, 9, 25, 3, 0)
    assert due_for_backup(Row(), datetime(2026, 9, 25, 20, 0), today) is False


def test_due_again_next_day():
    yesterday = datetime(2026, 9, 24, 3, 0)
    assert due_for_backup(Row(), datetime(2026, 9, 25, 3, 1), yesterday) is True


def test_report_due_rules():
    assert due_for_report(True, time(9, 0), "day", datetime(2026, 9, 25, 9, 0), None) is True
    assert due_for_report(False, time(9, 0), "day", datetime(2026, 9, 25, 9, 0), None) is False
    assert due_for_report(True, time(9, 0), "day", datetime(2026, 9, 25, 8, 59), None) is False
    sent = datetime(2026, 9, 25, 9, 0)
    assert due_for_report(True, time(9, 0), "day", datetime(2026, 9, 25, 18, 0), sent) is False
    yesterday = datetime(2026, 9, 24, 9, 0)
    assert due_for_report(True, time(9, 0), "day", datetime(2026, 9, 25, 9, 0), yesterday) is True


def test_week_report_is_not_due_on_day_after_and_due_after_seven_days():
    last_scheduled = datetime(2026, 9, 24, 9, 0)
    assert due_for_report(True, time(9, 0), "week",
                           datetime(2026, 9, 25, 9, 0), last_scheduled) is False
    assert due_for_report(True, time(9, 0), "week",
                           datetime(2026, 10, 1, 9, 0), last_scheduled) is True


def test_read_interval_is_clamped(db_session):
    row = get_backup_settings(db_session)
    row.check_interval_minutes = 0
    db_session.commit()
    assert read_interval_minutes(db_session) == 1
    row.check_interval_minutes = 100000
    db_session.commit()
    assert read_interval_minutes(db_session) == 1440


def test_run_due_jobs_stores_the_supplied_time(db_session):
    get_backup_settings(db_session)
    moment = datetime(2026, 9, 25, 2, 0)
    result = run_due_jobs(db_session, now=moment)
    assert result["backup"] is None
    assert result["report"] is None
    assert get_backup_settings(db_session).last_check_at == moment


def test_run_due_jobs_runs_backup_when_due(db_session, tmp_path, monkeypatch):
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    row.enabled = True
    row.schedule_time = time(3, 0)
    db_session.commit()
    monkeypatch.setattr("app.modules.backup.service.find_binary", lambda name, configured=None: None)
    result = run_due_jobs(db_session, now=datetime(2026, 9, 25, 3, 30))
    assert result["backup"] is not None
    assert result["backup"]["kind"] == "scheduled"


def test_run_due_jobs_survives_failure(db_session, monkeypatch):
    get_backup_settings(db_session)

    def boom(*args, **kwargs):
        raise RuntimeError("планировщик сломался")

    monkeypatch.setattr("app.modules.backup.scheduler.get_backup_settings", boom)
    result = run_due_jobs(db_session, now=datetime(2026, 9, 25, 3, 30))
    assert result["backup"] is None
    assert result["report"] is None


def test_run_due_jobs_skips_jobs_during_maintenance(db_session, tmp_path, monkeypatch):
    import app.modules.backup.scheduler as scheduler

    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    row.enabled = True
    row.schedule_time = time(3, 0)
    db_session.commit()
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_PERIOD_KEY, "day")
    set_setting(db_session, REPORT_TIME_KEY, "02:00")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, "[]")
    monkeypatch.setattr(scheduler, "create_backup", lambda *args, **kwargs: pytest.fail("backup must be skipped"))
    monkeypatch.setitem(__import__("sys").modules, "app.modules.backup.report", type(
        "M", (), {"send_report": staticmethod(lambda *args, **kwargs: pytest.fail("report must be skipped"))}))
    begin_countdown(120)
    try:
        result = run_due_jobs(db_session, now=datetime(2026, 9, 25, 3, 30))
    finally:
        set_idle()

    assert result == {"backup": None, "report": None}


def test_run_due_jobs_stamps_scheduled_report_run(db_session, tmp_path, monkeypatch):
    import app.modules.backup.scheduler as scheduler

    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    row.enabled = False
    db_session.commit()
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_PERIOD_KEY, "week")
    set_setting(db_session, REPORT_TIME_KEY, "02:00")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, "[]")
    report_module = type("M", (), {"send_report": staticmethod(lambda db, now=None: {"sent": 0, "skipped": 0, "failed": 0})})
    monkeypatch.setitem(__import__("sys").modules, "app.modules.backup.report", report_module)
    now = datetime(2026, 9, 25, 3, 30)
    result = run_due_jobs(db_session, now=now)

    assert result["report"] == {"sent": 0, "skipped": 0, "failed": 0}
    assert get_backup_settings(db_session).last_report_scheduled_at == now
    assert scheduler is not None


def test_backup_failure_does_not_skip_report(db_session, tmp_path, monkeypatch):
    import app.modules.backup.scheduler as scheduler
    row = get_backup_settings(db_session)
    row.local_path = str(tmp_path)
    row.enabled = True
    row.schedule_time = time(3, 0)
    db_session.commit()
    set_setting(db_session, REPORT_ENABLED_KEY, "true")
    set_setting(db_session, REPORT_TIME_KEY, "02:00")
    set_setting(db_session, REPORT_RECIPIENTS_KEY, "[]")

    def broken_backup(*args, **kwargs):
        raise RuntimeError("dump упал")

    monkeypatch.setattr(scheduler, "create_backup", broken_backup)
    report_module = type("M", (), {"send_report": staticmethod(lambda db, now=None: {"sent": 0, "skipped": 0, "failed": 0})})
    monkeypatch.setitem(__import__("sys").modules, "app.modules.backup.report", report_module)
    result = run_due_jobs(db_session, now=datetime(2026, 9, 25, 3, 30))
    assert result["backup"] is None
    assert result["report"] == {"sent": 0, "skipped": 0, "failed": 0}
    assert "backup:" in get_backup_settings(db_session).last_error
