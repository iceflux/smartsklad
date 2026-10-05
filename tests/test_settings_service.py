from pathlib import Path

from app.modules.audit.models import AuditLog
from app.modules.settings.service import (
    DEFAULT_MIN_STOCK_DAYS,
    DEFAULT_SEARCH_SLOW_MS,
    MIN_STOCK_DAYS_KEY,
    SEARCH_SLOW_MS_KEY,
    get_int_setting,
    get_setting,
    search_slow_ms,
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


def test_the_search_threshold_default_is_exactly_1000(db_session):
    """Ровно 1000, а не «округлённое число около 1000».

    Константа и сид проверяются вместе: `_defaults()` — единственное место,
    где живёт значение по умолчанию, и проверка только сида пропустила бы
    расхождение между ним и константой, на которую ссылается всё остальное.
    """
    assert DEFAULT_SEARCH_SLOW_MS == 1000, (
        "дефолт порога поиска изменился — прочитай обоснование в search_slow_ms() "
        "и отчёт раунда, прежде чем ставить другое число"
    )
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1000"
    assert search_slow_ms(db_session) == 1000


def test_the_search_threshold_default_is_seeded_on_a_fresh_database(db_engine):
    """Сид проверяется на пустой базе, а не на фикстуре.

    `db_session` уже прошла `ensure_default_settings`, поэтому «значение есть»
    ничего не говорит о том, откуда оно взялось. Здесь таблица пустая, и
    сид обязан дописать ключ сам.
    """
    from sqlalchemy.orm import sessionmaker

    from app.modules.settings.models import Setting
    from app.modules.settings.service import ensure_default_settings

    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with Session() as fresh:
        fresh.query(Setting).delete()
        fresh.commit()
        assert fresh.query(Setting).filter(Setting.key == SEARCH_SLOW_MS_KEY).first() is None
        ensure_default_settings(fresh)
        assert fresh.query(Setting).filter(Setting.key == SEARCH_SLOW_MS_KEY).first().value == "1000"


def test_the_search_threshold_survives_garbage_in_the_settings_table(db_session):
    """Мусор в таблице не должен попасть в страницу скриптом.

    Границы проверяет запись, но `set_setting` доступен и напрямую, а
    правка базы руками — обычное дело. На странице это привело бы к
    `var SEARCH_SLOW_MS = NaN`, и ручной режим не включался бы никогда.
    """
    set_setting(db_session, SEARCH_SLOW_MS_KEY, "мгновенно", user=None)
    assert search_slow_ms(db_session) == DEFAULT_SEARCH_SLOW_MS


def test_the_search_threshold_records_where_its_default_comes_from():
    """Обоснование дефолта — часть кода, а не пересказ в отчёте.

    1000 выглядит круглым числом, взятым на глаз, и следующая сессия
    «улучшит» его до 800 или 1500 молча. Проверка требует само решение
    (RAIL) и обе цифры, которые оно связывает, поэтому удаление или замена
    обоснования роняет прогон.
    """
    source = (Path(__file__).resolve().parents[1]
              / "app" / "modules" / "settings" / "service.py").read_text(encoding="utf-8")
    doc = source.split('def search_slow_ms(')[1].split('"""')[1]
    assert "RAIL" in doc, (
        "обоснование порога поиска потеряло ссылку на источник: 1000 перестанет "
        "выглядеть решением и станет магическим числом"
    )
    # Слово «RAIL» в тексте встречается дважды, поэтому проверяется вся
    # ссылка: подмена одного упоминания на другое не должна проходить, иначе
    # обоснование можно вычеркнуть по частям.
    assert "RAIL (Google" in doc, (
        "из обоснования ушла ссылка на источник границы — осталось только имя модели"
    )
    for part in ("1000", "Nielsen Norman Group", "200", "5000"):
        assert part in doc, f"в обосновании порога нет {part!r}"


def test_the_search_threshold_default_is_recorded_in_the_architecture_notes():
    """То же обоснование в документации, а не только в коде.

    Отчёт раунда читают раз, а `ARCHITECTURE.md` — каждый. Если число будет
    переписано молча, расхождение между кодом и архитектурой должно падать
    здесь, а не обнаруживаться через год.
    """
    notes = (Path(__file__).resolve().parents[1]
             / "system_docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    line = next(l for l in notes.splitlines() if SEARCH_SLOW_MS_KEY in l)
    assert "RAIL" in line and "1000" in line, line
