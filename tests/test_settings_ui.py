from urllib.parse import unquote

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.settings.service import (
    SEARCH_SLOW_MS_KEY,
    STOCK_TRACK_DEFAULT_KEY,
    get_setting,
    set_setting,
)

PASSWORD = "password123"


def _login(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


def _track_rest_checked(html: str) -> bool:
    """Отмечена ли галочка «Учитывать остатки при создании товаров»?

    Ищет тег по имени поля, а не сверяет текст тега целиком: порядок атрибутов
    в разметке — дело шаблона, а не смысл проверки. Раньше здесь стояла точная
    строка, и любая правка шаблона (добавление autocomplete) роняла тест, хотя
    поведение не менялось.
    """
    import re
    tag = next((t for t in re.findall(r"<input[^<>]*>", html)
                if 'name="track_rest_default"' in t), None)
    return tag is not None and "checked" in tag


def test_settings_page_403_without_right(client, db_session):
    _login(client, db_session, "seller1", "seller")  # seller не имеет settings.change
    r = client.get("/settings")
    assert r.status_code == 403


def test_settings_page_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "stock_track_default" not in r.text  # скрываем ключ, показываем лейбл
    assert "Учитывать остатки" in r.text


def test_settings_page_post_changes(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "track_rest_default": "false", "user_password_charset": "ABC123",
        "user_password_length": "12", "min_stock_days": "14", "search_slow_ms": "1000",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Сохранено" in unquote(r.headers["location"])


def test_new_product_form_shows_checkbox(client, db_session):
    _admin(client, db_session)
    r = client.get("/products/new")
    assert r.status_code == 200
    assert "Учитывать остатки" in r.text


def test_detail_shows_track_rest(client, db_session):
    _admin(client, db_session)
    r = client.post("/api/products", json={
        "name": "Вирт", "sku": "VIRT-UI", "price": 1.0, "rest": 0, "track_rest": False,
    })
    assert r.status_code == 201
    pid = r.json()["id"]
    r2 = client.get(f"/products/{pid}")
    assert r2.status_code == 200
    assert "нет" in r2.text  # «Учёт остатков: нет»


def test_settings_shows_the_telegram_token_in_a_closed_field(client, db_session):
    """Токен едет в поле, чтобы человек видел, что он сохранён.

    Раньше токен отдавался маской и в поле не попадал, из-за чего страница
    выглядела так, будто настройки нет. На экране поле закрыто (`type="password"`),
    а маска в API и журнале осталась — это проверяет test_smtp_settings.
    """
    _admin(client, db_session)
    set_setting(db_session, "telegram_bot_token", "1234567890:ABC", user=None)
    r = client.get("/settings")
    assert r.status_code == 200
    assert 'type="password" name="telegram_bot_token" value="1234567890:ABC"' in r.text


def test_clearing_the_telegram_fields_actually_clears_them(client, db_session):
    """Владелец обнаружил: токен и чат невозможно очистить — значение возвращалось.

    Причина была в маршруте, а не в форме: поля читались как `if telegram_token:`
    и при пустом значении просто не записывались, то есть старое оставалось, а
    страница выглядела как сохранённая пустая. Пустое значение здесь законно —
    это «канал не настроен», а не «оставь как было».

    Проверка идёт от поведения к странице: реальный POST с пустыми полями обязан
    стереть оба значения и показать канал ненастроенным.
    """
    from app.modules.settings.service import get_setting

    _admin(client, db_session)
    set_setting(db_session, "telegram_bot_token", "1234567890:ABC", user=None)
    set_setting(db_session, "telegram_chat_id", "-100123", user=None)

    r = client.post("/settings", data={"telegram_bot_token": "",
                                       "telegram_chat_id": "",
                                       "user_password_charset": "abcdef",
                                       "user_password_length": "12",
                                       "min_stock_days": "14",
                                       "search_slow_ms": "1000",
                                       "stock_track_default": "true"},
                    follow_redirects=False)
    assert r.status_code == 303, r.status_code
    assert "Ошибка" not in r.headers.get("location", ""), (
        f"пост отбит валидацией, поля не записаны: {r.headers.get('location')}")

    assert get_setting(db_session, "telegram_bot_token") == "", (
        "пустое поле обязано стирать токен, а не игнорироваться")
    assert get_setting(db_session, "telegram_chat_id") == "", (
        "пустое поле обязано стирать чат получателя")

    body = client.get("/settings").text
    assert 'name="telegram_bot_token" value="' in body, (
        "после очистки поле токена обязано быть пустым")
    assert "-100123" not in body, "старый чат продолжает светиться в форме"


def test_the_settings_form_has_no_placeholders(client, db_session):
    """На форме настроек не должно быть ни одного placeholder.

    Владелец заметил, что два поля из девятнадцати несли подсказку внутри, а
    остальные — нет, и вид формы был неоднородным. Подсказка перенесена в текст
    под полем, потому что состояние секрета и раньше там дублировалось.

    Почему не наоборот — «добавить placeholder во все поля»: у семнадцати полей
    есть `value`, а placeholder показывается только на пустом поле, то есть на
    них он был бы невидим. Единообразие достигается только удалением.
    """
    import re

    _admin(client, db_session)
    set_setting(db_session, "telegram_bot_token", "1234567890:ABC", user=None)
    r = client.get("/settings")
    assert r.status_code == 200

    form = re.search(r'<form method="post" action="/settings".*?</form>', r.text, re.S)
    assert form, "форма настроек не найдена"
    assert "placeholder" not in form.group(0), (
        "на форме настроек остались подсказки внутри полей")

    # Состояние секрета обязано остаться видимым — иначе очистку нечем подтвердить.
    assert 'name="telegram_bot_token" value="1234567890:ABC"' in r.text, "пропал токен из поля"


def test_no_field_on_the_settings_form_can_be_filled_by_the_browser(client, db_session):
    """Браузер не должен подставлять в настройки сохранённые учётные данные.

    Владелец обнаружил: в поле «Telegram-бот (токен)» само появлялся сохранённый
    пароль от сайта, а в «Логин (пользователь почты)» — сохранённый логин. Причина
    в том, что токен объявлен `type="password"` (чтобы не светить его на экране),
    и браузер на поле пароля реагирует как на пароль приложения. Хуже того,
    отправка формы затирала настоящий токен подставленным, то есть настройка
    «не сохранялась» без всякой видимой причины.

    Защита: у каждого поля формы задан `autocomplete`, причём у обоих секретов
    (`telegram_bot_token`, `smtp_password`) — `new-password`, потому что `off`
    браузер для паролей игнорирует. Требование «у каждого», а не «у этих двух»:
    форма настроек не должна автозаполняться ни в одном поле — здесь нет ни
    одного, куда вставленные браузером данные имели бы смысл.
    """
    import re

    _admin(client, db_session)
    r = client.get("/settings")
    assert r.status_code == 200

    form = re.search(r'<form method="post" action="/settings".*?</form>', r.text, re.S)
    assert form, "форма настроек не найдена"
    assert 'autocomplete="off"' in form.group(0).split(">")[0], (
        "на форме настроек должно быть выключено автозаполнение")

    for tag in re.findall(r"<input[^<>]*>", form.group(0)):
        assert "autocomplete" in tag, f"поле без autocomplete, браузер его заполнит: {tag}"

    for name in ("telegram_bot_token", "smtp_password"):
        tag = next(t for t in re.findall(r"<input[^<>]*>", form.group(0))
                   if f'name="{name}"' in t)
        assert 'autocomplete="new-password"' in tag, (
            f"{name} — секрет, для него нужен new-password, а не off: {tag}")


def test_a_secret_field_is_never_labelled(client, db_session):
    """Под полями секретов нет ни одной надписи — ни в одном состоянии.

    Владелец убрал их в два приёма: сначала «не настроено» и «не задан» — потому
    что пустое поле и так говорит, что ничего нет, — потом и «настроено», потому
    что точки в поле говорят то же самое про заполненное. Подпись под секретом
    не сообщала ничего, чего не сообщало бы само поле.

    Проверка обоих состояний заодно: надпись, поставленная наоборот (зелёная в
    пустом поле), прошла бы проверку «в настроенном есть», поэтому пустое
    состояние тоже проверяется на отсутствие.
    """
    import re

    _admin(client, db_session)
    form_of = lambda: re.search(
        r'<form method="post" action="/settings".*?</form>',
        client.get("/settings").text, re.S).group(0)

    for label in ("настроено", "не настроено", "задан", "не задан"):
        assert label not in form_of(), f"подпись «{label}» вернулась под секретом"

    set_setting(db_session, "telegram_bot_token", "1234567890:ABC", user=None)
    set_setting(db_session, "smtp_password", "the-mail-password", user=None)
    filled = form_of()
    for label in ("настроено", "не настроено", "задан", "не задан"):
        assert label not in filled, f"подпись «{label}» появилась и у заполненного поля"


def test_settings_page_shows_password_generation_fields(client, db_session):
    _admin(client, db_session)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "user_password_charset" in r.text
    assert "user_password_length" in r.text


def test_settings_page_sets_password_charset_and_length(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC!123", "user_password_length": "10",
        "min_stock_days": "14", "search_slow_ms": "1000",
    }, follow_redirects=False)
    assert r.status_code == 303
    r2 = client.get("/settings")
    assert 'value="ABC!123"' in r2.text
    assert 'value="10"' in r2.text


def test_settings_page_rejects_bad_length(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "3",
        "min_stock_days": "14", "search_slow_ms": "1000",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ошибка" in unquote(r.headers["location"])
    r2 = client.get("/settings")
    assert 'value="3"' not in r2.text
    assert 'value="12"' in r2.text


def test_settings_page_rejects_invalid_values_without_saving_stock_tracking(client, db_session):
    _admin(client, db_session)
    before = get_setting(db_session, STOCK_TRACK_DEFAULT_KEY)
    r = client.post("/settings", data={
        "track_rest_default": "off", "user_password_charset": "ABC123",
        "user_password_length": "3", "min_stock_days": "14", "search_slow_ms": "1000",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ошибка" in unquote(r.headers["location"])
    db_session.expire_all()
    assert get_setting(db_session, STOCK_TRACK_DEFAULT_KEY) == before


def test_settings_page_rejects_nonnumeric_length(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "²",
        "min_stock_days": "14", "search_slow_ms": "1000",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ошибка" in unquote(r.headers["location"])


def test_settings_page_rejects_empty_charset(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "   ", "user_password_length": "12",
        "min_stock_days": "14", "search_slow_ms": "1000",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ошибка" in unquote(r.headers["location"])


def test_settings_page_accepts_length_boundaries(client, db_session):
    _admin(client, db_session)
    for length in ("8", "64"):
        r = client.post("/settings", data={
            "user_password_charset": "ABC123", "user_password_length": length,
            "min_stock_days": "14", "search_slow_ms": "1000",
        }, follow_redirects=False)
        assert r.status_code == 303
        assert "Сохранено" in unquote(r.headers["location"])
        assert f'value="{length}"' in client.get("/settings").text


def test_settings_page_shows_min_stock_days(client, db_session):
    _admin(client, db_session)
    r = client.get("/settings")
    assert r.status_code == 200
    assert 'name="min_stock_days"' in r.text
    assert 'value="14"' in r.text


def test_settings_page_saves_min_stock_days(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "21", "search_slow_ms": "1000",
    }, follow_redirects=False)
    assert r.status_code == 303
    r2 = client.get("/settings")
    assert 'value="21"' in r2.text


def test_settings_page_rejects_bad_min_stock_days(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "400",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ошибка" in unquote(r.headers["location"])
    assert 'value="14"' in client.get("/settings").text


def test_settings_page_rejects_nonnumeric_min_stock_days(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "два",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ошибка" in unquote(r.headers["location"])
    assert 'value="14"' in client.get("/settings").text


def test_settings_page_bad_min_stock_days_does_not_save_other_fields(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "track_rest_default": "off", "user_password_charset": "ABC123",
        "user_password_length": "12", "min_stock_days": "400",
    }, follow_redirects=False)
    assert r.status_code == 303
    r2 = client.get("/settings")
    assert _track_rest_checked(r2.text)


def test_settings_page_requires_min_stock_days(client, db_session):
    _admin(client, db_session)
    client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "21", "search_slow_ms": "1000",
    }, follow_redirects=False)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ошибка" in unquote(r.headers["location"])
    assert 'value="21"' in client.get("/settings").text


def test_settings_page_accepts_min_stock_days_boundaries(client, db_session):
    _admin(client, db_session)
    for value in ("0", "365"):
        r = client.post("/settings", data={
            "user_password_charset": "ABC123", "user_password_length": "12",
            "min_stock_days": value, "search_slow_ms": "1000",
        }, follow_redirects=False)
        assert r.status_code == 303
        assert "Сохранено" in unquote(r.headers["location"])
        assert f'value="{value}"' in client.get("/settings").text


def test_settings_page_shows_the_search_threshold(client, db_session):
    _admin(client, db_session)
    r = client.get("/settings")
    assert r.status_code == 200
    assert f'name="{SEARCH_SLOW_MS_KEY}"' in r.text
    assert 'value="1000"' in r.text


def test_settings_page_saves_the_search_threshold(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "14", "search_slow_ms": "1500",
    }, follow_redirects=False)
    assert "Сохранено" in unquote(r.headers["location"])
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1500"
    assert 'value="1500"' in client.get("/settings").text


def test_settings_page_refuses_a_threshold_above_the_ceiling(client, db_session):
    """Отказ, а не усечение: 60000 — это автопоиск, который не отключается
    никогда, то есть страховка выключена по незнанию администратора."""
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "14", "search_slow_ms": "60000",
    }, follow_redirects=False)
    assert "Ошибка" in unquote(r.headers["location"])
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1000"
    assert 'value="1000"' in client.get("/settings").text


@pytest.mark.parametrize("value", ["0", "-1", "199", "5001", "мгновенно", ""])
def test_settings_page_refuses_a_threshold_outside_the_range(client, db_session, value):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "14", "search_slow_ms": value,
    }, follow_redirects=False)
    assert "Ошибка" in unquote(r.headers["location"])
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == "1000"


def test_settings_page_accepts_the_threshold_boundaries(client, db_session):
    _admin(client, db_session)
    for value in ("200", "5000"):
        r = client.post("/settings", data={
            "user_password_charset": "ABC123", "user_password_length": "12",
            "min_stock_days": "14", "search_slow_ms": value,
        }, follow_redirects=False)
        assert "Сохранено" in unquote(r.headers["location"])
        assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == value


def test_settings_page_403_does_not_save_the_threshold(client, db_session):
    _login(client, db_session, "seller1", "seller")
    before = get_setting(db_session, SEARCH_SLOW_MS_KEY)
    r = client.post("/settings", data={
        "user_password_charset": "ABC123", "user_password_length": "12",
        "min_stock_days": "14", "search_slow_ms": "5000",
    }, follow_redirects=False)
    assert r.status_code == 403
    assert get_setting(db_session, SEARCH_SLOW_MS_KEY) == before


def test_a_refused_threshold_does_not_save_the_other_fields(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={
        "track_rest_default": "off", "user_password_charset": "ABC123",
        "user_password_length": "12", "min_stock_days": "14",
        "search_slow_ms": "60000",
    }, follow_redirects=False)
    assert "Ошибка" in unquote(r.headers["location"])
    assert _track_rest_checked(client.get("/settings").text)

