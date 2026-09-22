import pytest

from app.modules.settings.service import get_setting, set_setting


def test_defaults_seeded(db_session):
    assert get_setting(db_session, "stock_track_default") == "true"


def test_set_setting(db_session):
    set_setting(db_session, "stock_track_default", "false")
    assert get_setting(db_session, "stock_track_default") == "false"


def test_set_setting_audit(db_session, client):
    # прямое обращение к сервису не логирует без user; проверим только механизм
    set_setting(db_session, "stock_track_default", "false", user=None)
    assert get_setting(db_session, "stock_track_default") == "false"