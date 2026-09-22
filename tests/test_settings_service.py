from app.modules.audit.models import AuditLog
from app.modules.settings.service import get_setting, set_setting


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