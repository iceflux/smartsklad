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


def test_settings_shows_masked_telegram_token(client, db_session):
    _admin(client, db_session)
    set_setting(db_session, "telegram_bot_token", "1234567890:ABC", user=None)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "1234567890" not in r.text  # полный токен не светится
    assert "****" in r.text  # маска


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
    assert "не настроен" in body, (
        "после очистки канал обязан показываться как ненастроенный")
    assert "-100123" not in body, "старый чат продолжает светиться в форме"


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
    assert '<input type="checkbox" name="track_rest_default" checked' in r2.text


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
    assert '<input type="checkbox" name="track_rest_default" checked' in client.get("/settings").text

