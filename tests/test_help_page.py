"""Справка не имеет права разойтись с приложением.

Две беды ждут такую страницу. Первая — она описывает кнопку или адрес, которых нет:
человек идёт по справке и упирается в стену, и хуже того — считает справку врущей.
Вторая — она говорит «почта не работает» текстом, а через час администратор заполнил
настройки, и страница продолжает врать.

Первая ловится проверками: каждый адрес из текста справки отвечает, а каждая названная
кнопка лежит в том шаблоне, о котором сказано. Вторая ловится иначе — состояние каналов
не написано в шаблоне, а читается при каждом запросе из тех же настроек, на которых
стоит проверка самой отправки, и сверяется с ней тестом.

Чего проверки не делают: они не читают глазами смысл каждой фразы и не знают, верно ли
описание человеком, который им пользуется. Поэтому формулировки написаны так, чтобы
ошибиться было негде: сказано, что делает раздел, а не как называется каждая его
кнопка; кнопки перечислены только те, за существованием которых стоит проверка ниже.
Состояние каналов — единственное место, где страница утверждает о настройках, и оно
выведено из них же.
"""

import re
from pathlib import Path

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"
ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
HELP_TEMPLATE = TEMPLATES / "help.html"

ROLES = ["seller", "warehouseman", "manager", "admin"]

NOT_READY = "Не настроено"
READY = "Настроено"


def _login_as(client, db_session, username, role):
    user = User(username=username, password_hash=hash_password(PASSWORD),
                role_id=get_role_by_name(db_session, role).id)
    db_session.add(user)
    db_session.commit()
    client.cookies.set("session", login(db_session, username, PASSWORD).token)
    return user


def _admin(client, db_session):
    return _login_as(client, db_session, "boss", "admin")


def _article(body: str) -> str:
    """Текст справки без шапки и меню.

    Без этого в проверку попали бы ссылки из меню и из шапки, и она проверяла бы не
    справку, а весь сайт — то есть падала бы на любой правке меню, ничего не говоря
    о странице.
    """
    found = re.search(r'<main[^>]*data-help-body[^>]*>(.*)</main>', body, re.S)
    assert found, "у страницы справки нет блока с признаком data-help-body"
    return found.group(1)


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


class _Ok:
    def raise_for_status(self):
        return None


def _smtp(monkeypatch, value: str) -> None:
    import app.config
    monkeypatch.setattr(app.config.settings, "smtp_host", value)


def _mail(monkeypatch, *, host="", user="", sender="") -> None:
    import app.config
    monkeypatch.setattr(app.config.settings, "smtp_host", host)
    monkeypatch.setattr(app.config.settings, "smtp_user", user)
    monkeypatch.setattr(app.config.settings, "smtp_from", sender)


def _mail_settings(db_session, **values) -> None:
    """SMTP через настройки приложения, а не через `.env`.

    Отдельная подмена нужна, потому что оба пути закрывают одну и ту же дверь, и
    проверка, читающая только один из них, пропустила бы страницу, которая врёт
    при заполненных настройках и молчит при заполненном `.env`.
    """
    from app.modules.settings.service import set_setting

    for key, value in values.items():
        set_setting(db_session, f"smtp_{key}", value)


def _telegram(db_session, token: str, chat_id: str) -> None:
    from app.modules.settings.service import set_setting
    set_setting(db_session, "telegram_bot_token", token)
    set_setting(db_session, "telegram_chat_id", chat_id)


def _delivery(body: str) -> str:
    """Раздел справки о каналах.

    Считается слова «Не настроено» по всему размету нельзя: то же слово стоит в
    разделе «Чего пока нет», и проверка считала бы не состояние каналов, а два
    упоминания подряд.
    """
    start = body.index('id="delivery"')
    return body[start:body.index('id="backup"')]


def test_the_help_is_in_the_menu_and_readable_by_everyone_who_can_use_the_app(client, db_session):
    """Справка без единого права — намеренное решение, а не недосмотр.

    Объяснить, что делает разделы, должен иметь право тот, у кого их меньше всего:
    новичок без единого права иначе не прочитает страницу, которая для него и
    написана.
    """
    for index, role in enumerate(ROLES):
        _login_as(client, db_session, f"user{index}", role)
        assert 'href="/help"' in client.get("/orders").text, role
        assert client.get("/help").status_code == 200, role
    client.cookies.clear()
    refused = client.get("/help", follow_redirects=False)
    assert refused.status_code == 303
    assert refused.headers["location"] == "/login"


def test_a_person_without_permissions_still_sees_the_help(client, db_session):
    """Продавец не видит в меню ни настроек, ни резервных копий — и видит справку.

    Если бы пункт справки прятался за правом, как все остальные, её не увидел бы
    именно тот, кому она нужнее.
    """
    _login_as(client, db_session, "seller", "seller")
    menu = client.get("/orders").text
    assert 'href="/help"' in menu
    assert 'href="/backups"' not in menu, "продавец не должен видеть резервные копии"
    assert 'href="/settings"' not in menu, "продавец не должен видеть настройки"
    assert client.get("/help").status_code == 200


OUTSIDE_GROUPS = {"/", "/orders", "/search", "/help"}
"""Три страницы, до которых доходят не через пункты меню: корень — по названию
«СмартСклад» в шапке, заказы — по одноимённой ссылке рядом с меню, поиск — по лупе в
шапке. В группах их нет, и справка вправе на них ссылаться."""


def test_the_help_describes_exactly_the_sections_the_menu_has(client, db_session):
    """Справка не заводит своей навигации.

    Пункты меню — это `NAV_GROUPS` плюс те три страницы, до которых доходят из шапки.
    Справка обязана описывать ровно их: свой список разделов разошёлся бы с меню при
    первом же добавлении.
    """
    from app.modules.auth.dependencies import NAV_GROUPS

    _admin(client, db_session)
    linked = set(re.findall(r'href="(/[a-z]+)"', _article(client.get("/help").text)))
    groups = {href for _title, items in NAV_GROUPS for _label, href, _flag in items}
    outside = linked - groups - OUTSIDE_GROUPS
    assert not outside, f"справка ссылается на разделы, которых нет в меню: {sorted(outside)}"
    assert not groups - linked - {"/help"}, f"из меню не описаны: {sorted(groups - linked)}"


def test_every_address_the_help_links_to_answers(client, db_session):
    """Главная проверка страницы: адрес из текста справки должен открываться.

    Проверяется ответ страницы, а не «строка похожа на адрес». Ссылка на
    несуществующий или удалённый раздел даёт 404 — и справка врёт человеку ровно там,
    где он ей поверил.
    """
    _admin(client, db_session)
    article = _article(client.get("/help").text)
    targets = sorted({href.split("#")[0] for href in re.findall(r'href="(/[^"]*)"', article)})
    assert len(targets) >= 10, f"в справке всего {len(targets)} адресов — проверка ослабла"
    for href in targets:
        response = client.get(href, follow_redirects=False)
        assert response.status_code == 200, f"{href} отвечает {response.status_code}"
        assert 'href="/help"' in response.text, f"на {href} из справки нечем вернуться"


def test_every_anchor_the_help_points_at_exists(client, db_session):
    _admin(client, db_session)
    body = client.get("/help").text
    anchors = {href.split("#")[1] for href in re.findall(r'href="(/help#[^"]*)"', body)
               if "/help#" in href}
    anchors |= set(re.findall(r'href="#([^"]+)"', body))
    assert len(anchors) >= 10, f"в справке {len(anchors)} якорей — проверка ослабла"
    for anchor in sorted(anchors):
        assert f'id="{anchor}"' in body, f"якорь #{anchor} в справке не ведёт в никуда"


def test_every_section_is_reachable_from_the_contents_and_the_other_way_round(
        client, db_session):
    """Оглавление обязано совпадать с разделами, а не только ссылаться на них.

    Обратное направление уже проверено выше: якорь, которого нет, — висячая ссылка.
    Здесь другое: раздел, добавленный без строки в оглавлении, никем не будет замечен —
    человек до него просто не дойдёт, и страница станет длиннее, а не полезнее.
    Появление шестнадцатого раздела обязано ронять прогон, пока строка в оглавлении
    не появится.
    """
    _admin(client, db_session)
    article = _article(client.get("/help").text)
    sections = set(re.findall(r'<h2 id="([^"]+)"', article))
    listed = set(re.findall(r'href="#([^"]+)"', article))
    assert sections, "у справки нет ни одного раздела — проверка ослабла"
    assert sections == listed, (
        f"разделы без строки в оглавлении: {sorted(sections - listed)}; "
        f"строки оглавления без раздела: {sorted(listed - sections)}")


CONTROLS = [
    ("Новый заказ", "base.html"),
    ("Печать", "order_detail.html"),
    ("Предпросмотр печати", "order_detail.html"),
    ("Скачать PDF", "order_detail.html"),
    ("На email", "order_detail.html"),
    ("В Telegram", "order_detail.html"),
    ("Создать копию сейчас", "backups.html"),
    ("Отправить отчёт сейчас", "backups.html"),
    ("Запас в днях", "settings.html"),
    ("Порог медленного поиска", "settings.html"),
    ("только к закупке", "forecast.html"),
    ("Добавить товар", "products.html"),
]
"""Названные в справке элементы и шаблон, где они обязаны лежать.

Каждая строка означает: справка говорит «кнопка «Печать»», и «Печать» обязана быть в
`order_detail.html`. Если переименовать кнопку и забыть справку, прогон упадёт здесь — а
не на человеке, который её читает.
"""


@pytest.mark.parametrize("control, template", CONTROLS, ids=[c for c, _ in CONTROLS])
def test_a_control_the_help_names_exists_in_the_template_it_describes(control, template):
    help_text = _text(HELP_TEMPLATE.read_text(encoding="utf-8"))
    assert control in help_text, f"справка зря называет «{control}» — проще убрать имя"
    source = (TEMPLATES / template).read_text(encoding="utf-8")
    assert control in _text(source), (
        f"справка называет «{control}», а в {template} такого элемента нет: либо "
        "переименуйте кнопку, либо уберите имя из справки")


def test_the_help_does_not_use_the_words_of_the_code(client, db_session):
    """Человек, который открыл приложение впервые, не знает, что такое `track_rest`.

    Право в справке названо по-человечески — так его и показывает интерфейс, — и
    код права в тексте означал бы имя, которого человек не увидит нигде. Поэтому
    запрещены все коды из `PERMISSION_LABELS`, а не по одному: список берётся из
    карты подписей, и новое право попадёт в запрет само, без правки проверки.
    """
    from app.modules.auth.roles_router import PERMISSION_LABELS

    _admin(client, db_session)
    text = _text(_article(client.get("/help").text))
    for word in ("track_rest", "min_stock", "forecast_", "has_data",
                 "is_deleted", "NULL", "bool", *PERMISSION_LABELS):
        assert word not in text, f"в справке мелькнул термин кода: {word!r}"


def test_the_values_a_person_fills_in_survive_the_ban_on_code_words(client, db_session):
    """Запрет на термины кода не должен съесть то, что человек в самом деле вводит.

    `telegram_chat_id` — имя поля на странице настроек: подпись там «Telegram
    chat_id получателя», и в справке оно названо тем же. Проверка идёт от
    настройки к тексту и требует, чтобы текст содержал оба значения, — сравнение
    страницы с самой собой было бы проверкой, которая не может упасть.
    """
    _admin(client, db_session)
    source = HELP_TEMPLATE.read_text(encoding="utf-8")
    text = _text(_article(client.get("/help").text))
    settings_page = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
    for field in ("telegram_bot_token", "telegram_chat_id"):
        assert f'name="{field}"' in settings_page, f"настройки не содержат поля {field}"
        assert f"<code>{field}</code>" in source, (
            f"{field} назван в справке без <code>: значение, которое вводит человек, "
            "должно отличаться от термина кода")
        assert field in text


def test_the_help_does_not_repeat_the_forecast_window(client, db_session):
    """Объяснение колонок живёт в окне на самой странице прогноза.

    Две копии одного объяснения разъедутся при первом же изменении, и одна из них
    окажется враньём. Справка рассказывает, что прогноз делает, и отправляет к окну.
    """
    _admin(client, db_session)
    body = client.get("/help").text
    forecast = body[body.index('id="forecast"'):body.index('id="printing"')]
    for phrase in ("в сутки", "линия тренда", "14 отдельных дней", "минус то, что сейчас"):
        assert phrase not in _text(forecast), f"справка повторяет окно прогноза: {phrase!r}"
    assert "кнопка со знаком вопроса" in _text(forecast), (
        "справка должна сказать, где объяснены колонки, иначе ссылка на кнопку в окне "
        "прогноза ведёт читателя в никуда")
    page = client.get("/forecast").text
    assert 'href="/help#forecast"' in page, (
        "окно прогноза не ссылается на раздел справки — справка без обратной ссылки "
        "остаётся страницей, о которой никто не узнает")
    assert 'id="forecast"' in body


def test_the_channels_are_read_from_the_configuration_and_not_written_in_the_page(
        client, db_session, monkeypatch):
    """Страница обязана показывать состояние, а не утверждать его.

    Написать в шаблоне «почта не работает» — значит через час солгать: администратор
    заполнил настройки, а страница продолжает утверждать обратное. Проверка идёт от
    настроек к тексту и требует, чтобы текст поменялся; сравнение страницы с самой
    собой было бы проверкой, которая не может упасть.
    """
    _admin(client, db_session)
    empty = _delivery(client.get("/help").text)
    assert empty.count(NOT_READY) == 2, (
        f"ожидались два неготовых канала, найдено {empty.count(NOT_READY)}")
    assert empty.count("не задан") == 5, (
        f"непустых настроек прочитано как пустых: {empty.count('не задан')}, а полей пять")

    _mail(monkeypatch, host="smtp.example.org", user="robot", sender="sales@example.org")
    _telegram(db_session, "123456:ABC", "-100123")
    filled = _delivery(client.get("/help").text)
    assert NOT_READY not in filled, (
        "настройки заполнены, а страница всё ещё пишет «Не настроено»")
    assert filled.count(READY) == 2, f"готовых каналов {filled.count(READY)}, а не два"
    assert "smtp.example.org" in filled, "заполненный сервер не показан"
    assert "не задан" not in filled, "после заполнения настроек страница пишет «не задан»"


def test_the_page_says_the_same_as_the_gate_that_refuses_the_send(
        client, db_session, monkeypatch):
    """Главный антивраньё этого проекта: страница и отправка читают одно и то же.

    Проверяется не «флажок выставлен», а что отправка действительно отказывает ровно
    тогда, когда страница пишет «Не настроено», и проходит дальше тогда, когда
    страница пишет «Настроено». Разойтись они могут, только если кто-то прочитает
    другое поле, — и этот тест ловит именно это.

    Оба пути настройки проверяются отдельно, потому что закрывают одну и ту же
    дверь: `.env` и настройки приложения. Проверка одного из них пропустила бы
    страницу, которая врала бы на второй машине — ровно на той, где администратор
    заполнил настройки вместо файла.
    """
    from app.modules.customers.models import Customer
    from app.modules.notifications import service as notifications
    from app.modules.orders.models import Order

    _admin(client, db_session)
    customer = Customer(name="ООО Ромашка", email="client@example.org")
    db_session.add(customer)
    db_session.commit()
    order = Order(number="00001", customer_id=customer.id, total_amount=100.0)
    db_session.add(order)
    db_session.commit()

    reached = []
    monkeypatch.setattr("app.modules.printing.service.render_order_pdf",
                        lambda *a, **k: b"%PDF")
    monkeypatch.setattr("app.modules.notifications.email.send_email",
                        lambda *a, **k: reached.append("email"))
    monkeypatch.setattr("httpx.post", lambda *a, **k: reached.append("telegram") or _Ok())

    def _try(channel):
        reached.clear()
        try:
            notifications.send_document(db_session, order, channel)
            return "sent"
        except ValueError as err:
            return str(err)

    _smtp(monkeypatch, "")
    assert "SMTP" in _try("email"), _try("email")
    assert reached == [], "отправка пошла, хотя сервер не задан"
    assert NOT_READY in client.get("/help").text
    _smtp(monkeypatch, "smtp.example.org")
    assert _try("email") == "sent"
    assert reached == ["email"], reached
    assert READY in client.get("/help").text

    # Тот же путь, но заполнены настройки приложения, а `.env` пуст.
    _smtp(monkeypatch, "")
    assert "SMTP" in _try("email"), (
        "сервер задан в настройках, а отправка его не видит — и страница врёт")
    assert NOT_READY in client.get("/help").text
    _mail_settings(db_session, host="smtp.example.org", sender="sales@example.org")
    assert _try("email") == "sent"
    assert reached == ["email"], reached
    body = client.get("/help").text
    assert READY in body, "настройки заполнены, а страница пишет «Не настроено»"
    assert "smtp.example.org" in body, "сервер из настроек не показан на странице"
    # Очистка настройки возвращает управление `.env`, и страница обязана сказать
    # ровно то же, что скажет отправка, — а не то, что было полминуты назад.
    _mail_settings(db_session, host="")
    _smtp(monkeypatch, "")
    assert "SMTP" in _try("email"), _try("email")
    assert NOT_READY in client.get("/help").text

    assert "Telegram не настроен" in _try("telegram")
    assert reached == [], "отправка пошла, хотя токен бота не задан"
    assert NOT_READY in client.get("/help").text
    _telegram(db_session, "123456:ABC", "-100123")
    assert _try("telegram") == "sent"
    assert reached == ["telegram"], reached
    assert READY in client.get("/help").text


def test_the_page_never_shows_the_mail_password_or_the_bot_token(client, db_session, monkeypatch):
    import app.config
    _admin(client, db_session)
    monkeypatch.setattr(app.config.settings, "smtp_host", "smtp.example.org")
    monkeypatch.setattr(app.config.settings, "smtp_password", "the-mail-password")
    _telegram(db_session, "123456:ABC", "-100123")
    body = client.get("/help").text
    for secret in ("123456:ABC", "-100123", "the-mail-password"):
        assert secret not in body, f"на странице справки виден секрет: {secret!r}"


MVP_TOPICS = ("Не настроено", "остаток нулевой", "мало данных", "сливаются в полосу",
              "забытого пароля", "заказах поставщику")
"""Ограничения, задуманные так: список из непроверяемых фраз прошёл бы сам по себе."""


def _section(body: str, start: str, end: str) -> str:
    return body[body.index(f'id="{start}"'):body.index(f'id="{end}"')]


def test_the_mvp_limits_and_the_known_problems_are_two_separate_lists(client, db_session):
    """Ограничения и дефекты — разные списки, и смешивать их нельзя.

    Проверяется не «список не пуст», а что каждый пункт лежит в своём списке:
    ограничение, уехавшее в «Известные проблемы», читается как поломка, а
    непереведённое сообщение — как задуманное ограничение. Разделы обязаны
    существовать и быть соседними, иначе срезом можно было бы поймать один и тот же
    текст дважды.
    """
    _admin(client, db_session)
    body = _article(client.get("/help").text)
    limits = _text(_section(body, "mvp", "problems"))
    problems = _text(body[body.index('id="problems"'):])
    for topic in MVP_TOPICS:
        assert topic in limits, f"в ограничениях нет: {topic!r}"
        assert topic not in problems, (
            f"ограничение {topic!r} попало в «Известные проблемы» — там дефекты")
    assert "Неизвестных дефектов нет" in problems or "на английском" in problems, (
        "раздел «Известные проблемы» ни пуст, ни отрицает наличие дефектов — непонятно, "
        "кто туда вписывается")
    assert "на английском" not in problems, (
        "страница утверждает, что сообщения остались на английском, а они переведены; "
        "прогон годами рассказывал бы о несуществующей проблеме")
    assert "переведён" not in problems and "непереведён" not in limits, (
        "в ограничениях или в дефектах снова появилось упоминание непереведённых "
        "сообщений — перевод сделан, и это враньё")
    assert "на английском" not in limits


ORDER_STATUS_COLUMNS = ("Статус", "Что означает", "Куда можно перейти")


def test_the_order_status_table_agrees_with_the_service(client, db_session):
    """Таблица статусов нарисована руками и обязана совпадать с переходами.

    Проверка идёт от `NEXT_POSSIBLE` и `STATUS_LABELS` к тексту: смена правила
    перехода без правки справки оставила бы читателя с неверной таблицей, и
    заметил бы это только тот, кто попробовал бы перейти.
    """
    from app.main import STATUS_LABELS
    from app.modules.orders.service import NEXT_POSSIBLE, VALID_STATUSES

    _admin(client, db_session)
    body = _article(client.get("/help").text)
    table = body[body.index("<table"):body.index("</table>")]
    header = tuple(_text(cell).strip() for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S))
    assert header == ORDER_STATUS_COLUMNS, f"шапка таблицы статусов: {header}"
    rows = [[_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)]
    rows = [row for row in rows if len(row) == 3]
    labels = [row[0] for row in rows]
    assert labels == [STATUS_LABELS[key] for key in ("new", "processing", "done", "cancelled")], (
        f"в таблице статусов {labels}, а в сервисе {list(STATUS_LABELS.values())}")
    assert set(VALID_STATUSES) == set(STATUS_LABELS), (
        "в сервисе появился статус, которого нет среди подписей: таблица справки "
        "перечисляет только эти четыре и новый молча не попадёт в неё")
    for label, _means, targets in rows:
        source = next(key for key, value in STATUS_LABELS.items() if value == label)
        allowed = {STATUS_LABELS[key] for key in NEXT_POSSIBLE.get(source, ())}
        listed = {part.strip() for part in targets.split(",")} - {"—"}
        assert listed == allowed, (
            f"у статуса «{label}» в справке переходы «{targets}», а сервис разрешает "
            f"{sorted(allowed) or 'ни одного'}")


TRANSLATED = [
    ("name required", "inventory/service.py"),
    ("inn must be 10 or 12 digits", "customers/service.py"),
    ("discount must be between 0 and 99", "customers/service.py"),
]
"""Сообщения, на которые справка прямо указывала как на английские, и файл, где
они жили.

Проверка теперь обратная: их быть не должно. Обратной она стала не по вкусу, а
потому что прямая проверяла «сообщение осталось английским» и годами держала
перевод запрещённым — зелёный прогон требовал непереведённого текста.

Модуль указан не для красоты: «name required» есть и в товарах, и в клиентах, и
проверка по всему `app` нашла бы его в другом файле и осталась зелёной после
перевода. Справка говорила «о пустом названии товара» — значит падать должна
именно товарная проверка.
"""


def test_the_messages_the_help_used_to_admit_to_are_translated():
    """Единственное место справки, утверждающее о коде, привязано к самому коду.

    Страница говорила, что часть сообщений об ошибках осталась на английском. Пока
    это было правдой, предложение проверялось прямой фразой «а они всё ещё
    английские». Теперь правда обратная, и проверка стала обратной же: как только
    сообщение вернётся на английский, прогон упадёт — иначе страница годами
    рассказывала бы о несуществующей проблеме.
    """
    for text, module in TRANSLATED:
        source = (ROOT / "app" / "modules" / module).read_text(encoding="utf-8")
        assert not any(f'"{text}"' in line for line in source.splitlines()), (
            f"сообщение {text!r} снова на английском в {module}")