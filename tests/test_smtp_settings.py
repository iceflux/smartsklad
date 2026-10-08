"""Пять значений SMTP заводятся администратором со страницы настроек.

Что здесь проверяется и почему именно так:

* сид пустой, как у ключей Telegram, иначе страница справки показывала бы
  «Настроено» при отправке, которая не уйдёт;
* настройка перекрывает `.env`, а пустая настройка уводит к нему, — переезд на
  интерфейс не должен сломать машину, где SMTP уже заполнен в файле;
* пароль не читается ни через API, ни из журнала аудита, а в аудите лежит маска;
* пустое поле пароля означает «оставить», а не «стереть», — иначе смена адреса
  сервера требовала бы вводить пароль заново при каждом заходе на страницу;
* порт отвергается целиком, а не усекается и не подгоняется под диапазон.

Каждая проверка сравнивает не страницу с самой собой, а страницу с тем, что
сервис реально делает: правка подмены обязана менять текст, иначе проверка не
может упасть (см. `test_the_substitutions_really_change_the_text`).
"""

from urllib.parse import unquote

import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.notifications.email import (
    SMTP_FROM_KEY,
    SMTP_HOST_KEY,
    SMTP_PASSWORD_KEY,
    SMTP_PORT_KEY,
    SMTP_PORT_MAX,
    SMTP_USER_KEY,
    smtp_config,
)
from app.modules.settings.models import Setting
from app.modules.settings.service import (
    MASKED_KEYS,
    ensure_default_settings,
    get_setting,
    set_setting,
)

PASSWORD = "password123"
FIELDS = {SMTP_HOST_KEY, SMTP_PORT_KEY, SMTP_USER_KEY, SMTP_PASSWORD_KEY, SMTP_FROM_KEY}


def _msg(response) -> str:
    """Текст сообщения из адреса возврата.

    `_with_msg` кладёт его в строку запроса, то есть процентно закодированным;
    сравнение с кириллицей по сырому адресу всегда было бы ложью.
    """
    return unquote(response.headers["location"])


def _login(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, username, PASSWORD).token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


def _audit_for(db_session, key):
    """Строки аудита по конкретной настройке.

    `entity_id` у `settings.update` — это id строки самой настройки, а журнал
    общий на все ключи, поэтому «последняя строка журнала» после сохранения формы
    относится к полю компании, а не к паролю.
    """
    setting_id = (db_session.query(Setting).filter(Setting.key == key).one().id)
    return (db_session.query(AuditLog)
            .filter(AuditLog.action == "settings.update", AuditLog.entity_id == setting_id)
            .order_by(AuditLog.id.desc()).all())


def _env(monkeypatch, **values):
    """Подмена значений `.env` — запасного пути.

    Имена — как в `.env` (`from`, не `sender`), иначе подмена молча создала бы
    новое поле, которого у `Settings` нет, и проверка падала бы не о том.
    """
    import app.config

    for name, value in values.items():
        monkeypatch.setattr(app.config.settings, f"smtp_{'from' if name == 'sender' else name}", value)


def _fill(**overrides):
    form = {
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "14", "search_slow_ms": "1000",
        SMTP_HOST_KEY: "", SMTP_PORT_KEY: "", SMTP_USER_KEY: "",
        SMTP_PASSWORD_KEY: "", SMTP_FROM_KEY: "",
    }
    form.update(overrides)
    return form


# --- сид ---------------------------------------------------------------


def test_the_five_keys_are_seeded_empty_like_the_telegram_ones(db_session):
    for key in FIELDS:
        assert get_setting(db_session, key) == "", f"{key} сидится не пустым"
    assert get_setting(db_session, "telegram_bot_token") == ""


def test_the_seeds_land_on_an_empty_database_too(db_engine):
    """Пустая база, а не фикстура: `db_session` уже прошла сид, поэтому «значение
    есть» ничего не говорит о том, откуда оно взялось."""
    from sqlalchemy.orm import sessionmaker

    from app.modules.settings.models import Setting

    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with Session() as fresh:
        fresh.query(Setting).delete()
        fresh.commit()
        ensure_default_settings(fresh)
        for key in FIELDS:
            row = fresh.query(Setting).filter(Setting.key == key).one()
            assert row.value == "", f"сид {key} = {row.value!r}"


def test_the_port_seed_is_empty_so_that_env_still_works(db_engine):
    """Порт сидится пустым, а не «587».

    Непустой порт в настройках перекрыл бы `SMTP_PORT` из `.env`, и запасной путь
    перестал бы работать ровно на той машине, где он нужен, — молча.
    """
    from sqlalchemy.orm import sessionmaker

    from app.modules.settings.models import Setting

    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with Session() as fresh:
        fresh.query(Setting).delete()
        fresh.commit()
        ensure_default_settings(fresh)
        assert fresh.query(Setting).filter(Setting.key == SMTP_PORT_KEY).one().value == ""


# --- права -------------------------------------------------------------


def test_a_seller_cannot_write_the_mail_settings(client, db_session):
    _login(client, db_session, "seller1", "seller")
    r = client.post("/settings", data=_fill(**{SMTP_HOST_KEY: "smtp.example.org"}),
                    follow_redirects=False)
    assert r.status_code == 403
    assert get_setting(db_session, SMTP_HOST_KEY) == "", "отказ по правам записал значение"


# --- чтение и запись ----------------------------------------------------


def test_the_page_shows_the_values_and_saves_new_ones(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data=_fill(**{
        SMTP_HOST_KEY: "smtp.example.org", SMTP_PORT_KEY: "2525",
        SMTP_USER_KEY: "robot", SMTP_FROM_KEY: "sales@example.org",
    }), follow_redirects=False)
    assert r.status_code == 303
    assert "Сохранено" in _msg(r)
    db_session.expire_all()
    assert get_setting(db_session, SMTP_HOST_KEY) == "smtp.example.org"
    assert get_setting(db_session, SMTP_PORT_KEY) == "2525"
    assert get_setting(db_session, SMTP_USER_KEY) == "robot"
    assert get_setting(db_session, SMTP_FROM_KEY) == "sales@example.org"

    page = client.get("/settings").text
    for value in ("smtp.example.org", "2525", "robot", "sales@example.org"):
        assert f'value="{value}"' in page, f"сохранённое {value!r} не показано на странице"


def test_the_saved_values_are_the_ones_used_to_send(client, db_session, monkeypatch):
    """Настройка обязана быть не строкой на странице, а тем, что читает отправка.

    Иначе администратор настроил бы всё и видел бы «Настройки», а письма уходили
    бы по-старому.
    """
    import app.modules.notifications.email as email_module

    seen = {}

    class _SMTP:
        def __init__(self, host, port, timeout=None):
            seen.update(host=host, port=port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            return None

        def login(self, user, password):
            seen.update(user=user, password=password)

        def send_message(self, msg):
            return None

    _env(monkeypatch, host="", port=587, user="", password="", sender="")
    _admin(client, db_session)
    client.post("/settings", data=_fill(**{
        SMTP_HOST_KEY: "smtp.example.org", SMTP_PORT_KEY: "2525",
        SMTP_USER_KEY: "robot", SMTP_PASSWORD_KEY: "the-mail-password",
        SMTP_FROM_KEY: "sales@example.org",
    }), follow_redirects=False)
    db_session.expire_all()
    monkeypatch.setattr(email_module.smtplib, "SMTP", _SMTP)
    email_module.send_email("client@example.org", "тема", "тело", db=db_session)
    assert seen == {"host": "smtp.example.org", "port": 2525,
                    "user": "robot", "password": "the-mail-password"}


# --- .env как запасной путь --------------------------------------------


def test_the_settings_win_over_env(db_session, monkeypatch):
    _env(monkeypatch, host="env.example.org", user="envuser")
    set_setting(db_session, SMTP_HOST_KEY, "settings.example.org")
    set_setting(db_session, SMTP_USER_KEY, "settingsuser")
    assert smtp_config(db_session)["host"] == "settings.example.org"
    assert smtp_config(db_session)["user"] == "settingsuser"


def test_env_is_used_when_the_setting_is_empty(db_session, monkeypatch):
    _env(monkeypatch, host="env.example.org", user="envuser", sender="env@example.org")
    assert smtp_config(db_session)["host"] == "env.example.org"
    assert smtp_config(db_session)["user"] == "envuser"
    assert smtp_config(db_session)["sender"] == "env@example.org"


def test_a_cleared_setting_hands_the_field_back_to_env(db_session, monkeypatch):
    """Очистка поля — это не «выключить», а «вернуть управление .env».

    Смысл прямой и проверяется прямой: запасной путь перестал бы существовать
    ровно тогда, когда он нужен, если бы очистка его отменяла.
    """
    _env(monkeypatch, host="env.example.org")
    set_setting(db_session, SMTP_HOST_KEY, "settings.example.org")
    assert smtp_config(db_session)["host"] == "settings.example.org"
    set_setting(db_session, SMTP_HOST_KEY, "")
    assert smtp_config(db_session)["host"] == "env.example.org"


def test_the_port_falls_back_to_env_and_then_to_the_default(db_session, monkeypatch):
    """Мусор в таблице настроек не должен доезжать до `smtplib`.

    Цепочка запасных путей одна и та же для всех пяти значений: настройка, потом
    `.env`, потом дефолт из `config.py`. Порт отличается только тем, что дефолт у
    него числовой, поэтому и проверяется отдельно.
    """
    _env(monkeypatch, port=2525)
    assert smtp_config(db_session)["port"] == 2525
    set_setting(db_session, SMTP_PORT_KEY, "1025")
    assert smtp_config(db_session)["port"] == 1025
    for junk in ("быстро", "-1", "0", "70000", "587.0"):
        set_setting(db_session, SMTP_PORT_KEY, junk)
        assert smtp_config(db_session)["port"] == 2525, junk
    _env(monkeypatch, port=587)
    set_setting(db_session, SMTP_PORT_KEY, "быстро")
    assert smtp_config(db_session)["port"] == 587


# --- пароль ------------------------------------------------------------


def test_the_password_is_masked_in_the_audit_log(client, db_session):
    _admin(client, db_session)
    client.post("/settings", data=_fill(**{SMTP_PASSWORD_KEY: "the-mail-password"}),
                follow_redirects=False)
    db_session.expire_all()
    row = _audit_for(db_session, SMTP_PASSWORD_KEY)[0]
    assert "the-mail-password" not in (row.new_value or ""), "пароль попал в аудит открытым"
    assert "****" in row.new_value
    # Сырец остаётся в таблице настроек — маска это только отображение.
    assert get_setting(db_session, SMTP_PASSWORD_KEY) == "the-mail-password"


def test_the_password_is_masked_in_the_audit_log_on_a_replacement(client, db_session):
    """Маскируются обе стороны: старый пароль тоже мог бы быть настоящим."""
    _admin(client, db_session)
    client.post("/settings", data=_fill(**{SMTP_PASSWORD_KEY: "первый-пароль"}),
                follow_redirects=False)
    client.post("/settings", data=_fill(**{SMTP_PASSWORD_KEY: "второй-пароль"}),
                follow_redirects=False)
    db_session.expire_all()
    rows = _audit_for(db_session, SMTP_PASSWORD_KEY)
    assert len(rows) == 2, f"строк аудита по паролю {len(rows)}, а не две"
    assert rows[0].old_value == "пер****оль"
    text = f"{rows[0].old_value} {rows[0].new_value}"
    for secret in ("первый-пароль", "второй-пароль"):
        assert secret not in text, f"{secret!r} попал в аудит открытым"
    assert text.count("****") == 2


def test_the_api_never_returns_the_password(client, db_session):
    _admin(client, db_session)
    set_setting(db_session, SMTP_PASSWORD_KEY, "the-mail-password")
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert "the-mail-password" not in r.text, "пароль почты вернулся из API открытым"
    row = next(s for s in r.json() if s["key"] == SMTP_PASSWORD_KEY)
    assert "****" in row["value"]
    # Настройки, не являющиеся секретами, по-прежнему читаются.
    assert any(s["key"] == "stock_track_default" for s in r.json())


def test_the_password_reaches_the_page_only_as_a_closed_field(client, db_session):
    """Секрет отдаётся в поле, но остаётся закрытым на экране.

    Раньше значение не отдавалось вовсе, и человек правил настройки вслепую:
    поле выглядело пустым, хотя пароль был сохранён. Теперь значение в поле
    есть, а `type="password"` показывает точки, так что на экране пароля нет.

    Честная оговорка: значение лежит в HTML, то есть в исходнике страницы его
    видно. Это осознанный размен — видимость значения для человека против
    секретности исходника. Секретность, которая осталась в силе, проверяется
    отдельно: API и журнал по-прежнему отдают маску.
    """
    _admin(client, db_session)
    set_setting(db_session, SMTP_PASSWORD_KEY, "the-mail-password")
    page = client.get("/settings").text
    assert 'type="password" name="smtp_password" value="the-mail-password"' in page


def test_the_api_and_the_audit_still_hide_the_password(client, db_session):
    """Раз секрет теперь есть в HTML страницы, маска обязана остаться в другом
    месте: `/api/settings` и журнал изменений — их читает не только владелец."""
    _admin(client, db_session)
    set_setting(db_session, SMTP_PASSWORD_KEY, "the-mail-password")
    r = client.get("/api/settings")
    assert "the-mail-password" not in r.text, "пароль почты вернулся из API открытым"
    assert SMTP_PASSWORD_KEY in {s["key"] for s in r.json()}


def test_the_settings_page_does_not_label_the_password(client, db_session):
    """Под полем пароля нет надписи ни когда он есть, ни когда его нет.

    Надписи убраны по требованию владельца в два приёма: сначала «не задан»
    (пустое поле и так говорит), потом и «задан» (точки в поле говорят то же
    про заполненное). Проверяется оба состояния, иначе надпись, поставленная
    наоборот, прошла бы проверку по одному из них.
    """
    import re

    _admin(client, db_session)
    form_of = lambda: re.search(
        r'<form method="post" action="/settings".*?</form>',
        client.get("/settings").text, re.S).group(0)

    set_setting(db_session, SMTP_PASSWORD_KEY, "the-mail-password")
    assert "задан" not in form_of()

    set_setting(db_session, SMTP_PASSWORD_KEY, "")
    assert "задан" not in form_of()


def test_the_password_key_is_in_the_one_masked_list():
    """Одна маска на оба пути, а не по одной на секрет.

    Завести вторую маску — значит разрешить себе новый секрет и забыть про него;
    этот список читается при каждой записи настройки, то есть забыть его нельзя.
    """
    assert SMTP_PASSWORD_KEY in MASKED_KEYS
    assert "telegram_bot_token" in MASKED_KEYS


# --- очистка пароля -----------------------------------------------------


def test_an_empty_password_field_clears_the_password(client, db_session):
    """Очистил поле — сохранил — пароля нет.

    Раньше пустое поле означало «оставить», а стирать надо было галочкой, и для
    этого пришлось завести отдельный контрол. Владелец назвал это перебором: поле
    и так видно, очистил — сохранил.
    """
    _admin(client, db_session)
    client.post("/settings", data=_fill(**{SMTP_PASSWORD_KEY: "первый-пароль"}),
                follow_redirects=False)
    client.post("/settings", data=_fill(**{SMTP_HOST_KEY: "другой.example.org"}),
                follow_redirects=False)
    db_session.expire_all()
    assert get_setting(db_session, SMTP_PASSWORD_KEY) == "", (
        "пустое поле обязано стирать пароль, а не молча оставлять прежний")
    assert get_setting(db_session, SMTP_HOST_KEY) == "другой.example.org"


def test_the_password_survives_a_change_of_another_field(client, db_session):
    """Правка соседнего поля не должна затирать пароль.

    Раз пароль теперь едет в форме, смена адреса сервера его приносит с собой —
    но это поведение страницы, а не сервера: сервер получает то, что в полях.
    Проверяется ровно то, что форма отдаёт значение на сервер при повторном
    сохранении, иначе «очистил одно поле — потерял другое» стало бы реальной
    поломкой интерфейса.
    """
    _admin(client, db_session)
    set_setting(db_session, SMTP_PASSWORD_KEY, "the-mail-password")
    page = client.get("/settings").text
    assert 'name="smtp_password" value="the-mail-password"' in page
    assert 'name="smtp_password" value=""' not in page, (
        "поле пароля должно приходить заполненным, иначе правка любого соседнего "
        "поля стёрла бы пароль")


def test_there_is_no_clear_checkbox_left(client, db_session):
    """Галочка удалена вместе с «оставлять прежний пароль» — обе вещи ушли вместе."""
    _admin(client, db_session)
    page = client.get("/settings").text
    assert "smtp_password_clear" not in page
    assert "Стереть сохранённый пароль" not in page


# --- порт ---------------------------------------------------------------


@pytest.mark.parametrize("value", ["быстро", "587.0", "+587", "٥٨٧", "-1",
                                   "0", "65536", "70000", "1e3", "0x24b"])
def test_the_port_refuses_nonsense_without_saving(client, db_session, value):
    """Отказ, а не усечение: молча подогнанное значение выглядело бы как
    сохранённое и вело бы себя не так, как показано в поле."""
    _admin(client, db_session)
    client.post("/settings", data=_fill(**{SMTP_PORT_KEY: "1025"}), follow_redirects=False)
    db_session.expire_all()
    r = client.post("/settings", data=_fill(**{SMTP_PORT_KEY: value}),
                    follow_redirects=False)
    db_session.expire_all()
    assert "Ошибка" in _msg(r), value
    assert get_setting(db_session, SMTP_PORT_KEY) == "1025", value


@pytest.mark.parametrize("value,stored", [("587 ", "587"), ("  2525  ", "2525")])
def test_the_port_keeps_only_the_padding_off_the_edges(client, db_session, value, stored):
    """Пробелы вокруг числа — не «мусор», а то же самое число.

    Отличать их от «587.0» пришлось бы отдельным правилом в браузере, а человек,
    скопировавший порот из документации вместе с пробелом, получил бы отказ,
    который нечего понять.
    """
    _admin(client, db_session)
    r = client.post("/settings", data=_fill(**{SMTP_PORT_KEY: value}),
                    follow_redirects=False)
    db_session.expire_all()
    assert "Ошибка" not in _msg(r), value
    assert get_setting(db_session, SMTP_PORT_KEY) == stored


@pytest.mark.parametrize("value", ["1", "587", "2525", str(SMTP_PORT_MAX)])
def test_the_port_accepts_the_whole_range(client, db_session, value):
    _admin(client, db_session)
    r = client.post("/settings", data=_fill(**{SMTP_PORT_KEY: value}),
                    follow_redirects=False)
    db_session.expire_all()
    assert "Ошибка" not in _msg(r), value
    assert get_setting(db_session, SMTP_PORT_KEY) == value


def test_an_empty_port_hands_the_field_back_to_env(client, db_session, monkeypatch):
    _env(monkeypatch, port=2525)
    _admin(client, db_session)
    client.post("/settings", data=_fill(**{SMTP_PORT_KEY: "1025"}), follow_redirects=False)
    client.post("/settings", data=_fill(), follow_redirects=False)
    db_session.expire_all()
    assert get_setting(db_session, SMTP_PORT_KEY) == ""
    assert smtp_config(db_session)["port"] == 2525


def test_a_refused_port_does_not_save_the_other_fields(client, db_session):
    """Отказ возвращает страницу, не записав ничего, — как и любой другой отказ
    этого экрана."""
    _admin(client, db_session)
    client.post("/settings", data=_fill(**{SMTP_HOST_KEY: "smtp.example.org"}),
                follow_redirects=False)
    client.post("/settings", data=_fill(**{SMTP_HOST_KEY: "другой.example.org",
                                           SMTP_PORT_KEY: "быстро"}),
                follow_redirects=False)
    db_session.expire_all()
    assert get_setting(db_session, SMTP_HOST_KEY) == "smtp.example.org"
    assert get_setting(db_session, SMTP_PORT_KEY) == ""


# --- подмена в проверках ------------------------------------------------


def test_the_substitutions_really_change_the_text(monkeypatch):
    """Сама охрана охраны: без неё «проверка, что текст изменился» ничего не значит.

    Каждая подмена в этом файле — замена значения, которое проверка читает. Если
    подстановка перестала менять текст, все проверки ниже стали бы проверками
    страницы с самой собой, то есть непроверяемыми.
    """
    for value in ("быстро", "587.0", "+587", "٥٨٧", "-1", "0", "65536", "1e3"):
        assert f'value="{value}"' != f'value="1025"', value
    assert _fill(**{SMTP_PORT_KEY: "1025"})[SMTP_PORT_KEY] == "1025"
    assert _fill()[SMTP_PORT_KEY] == ""