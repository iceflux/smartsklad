from app.modules.audit.models import AuditLog
from app.modules.settings.service import (
    DEFAULT_MIN_STOCK_DAYS,
    MIN_STOCK_DAYS_KEY,
    get_int_setting,
    get_setting,
    set_setting,
)


def test_defaults_seeded(db_session):
    assert get_setting(db_session, "stock_track_default") == "true"


def test_set_setting(db_session):
    set_setting(db_session, "stock_track_default", "false")
    assert get_setting(db_session, "stock_track_default") == "false"


def test_set_setting_audit(db_session):
    set_setting(db_session, "stock_track_default", "false", user=None)
    row = (db_session.query(AuditLog)
           .filter(AuditLog.action == "settings.update",
                   AuditLog.entity_type == "settings")
           .first())
    assert row is not None
    assert row.old_value == "true"
    assert row.new_value == "false"
    assert row.user_id is None


def test_set_setting_token_masked_in_audit(db_session):
    set_setting(db_session, "telegram_bot_token", "1234567890:ABCDEF", user=None)
    row = (db_session.query(AuditLog)
           .filter(AuditLog.action == "settings.update",
                   AuditLog.entity_type == "settings")
           .order_by(AuditLog.id.desc()).first())
    assert row is not None
    assert "1234567890:ABCDEF" not in (row.new_value or "")
    assert "****" in row.new_value
    # сырец остаётся в таблице настроек, маска — только в аудите
    assert get_setting(db_session, "telegram_bot_token") == "1234567890:ABCDEF"


def test_default_min_stock_days_seeded(db_session):
    assert get_setting(db_session, MIN_STOCK_DAYS_KEY) == "14"


def test_get_int_setting_parses_and_falls_back(db_session):
    set_setting(db_session, MIN_STOCK_DAYS_KEY, "21", user=None)
    assert get_int_setting(db_session, MIN_STOCK_DAYS_KEY, DEFAULT_MIN_STOCK_DAYS) == 21
    set_setting(db_session, MIN_STOCK_DAYS_KEY, "не число", user=None)
    assert get_int_setting(db_session, MIN_STOCK_DAYS_KEY, DEFAULT_MIN_STOCK_DAYS) == DEFAULT_MIN_STOCK_DAYS


def test_get_int_setting_falls_back_when_key_absent(db_session):
    assert get_int_setting(db_session, "no_such_setting_key", DEFAULT_MIN_STOCK_DAYS) == DEFAULT_MIN_STOCK_DAYS
