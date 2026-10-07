"""Кнопка-вопрос у таблицы прогноза.

Владелец спросил, что значат девять колонок, и девять подсказок по одной
словами ему ничего не давали: он не мог сопоставить их между собой. Кнопка
открывает одно окно и говорит там о том, что действительно спрашивали, — а не
пересказывает шапку.

Окно не новое: те же `.app-modal` / `.app-modal-panel`, что у поиска из шапки и
у окон выбора в заказе. Второй механизм означал бы две разные разметки одного
понятия, и разъехались бы они при первом же изменении. Фокус не заперт: в этом
проекте решение принято и держится на трёх уже существующих окнах.
"""

import re
from pathlib import Path

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "templates" / "forecast.html"
CSS = ROOT / "app" / "static" / "app.css"


def _admin(client, db_session):
    db_session.add(User(username="boss", password_hash=hash_password(PASSWORD),
                        role_id=get_role_by_name(db_session, "admin").id))
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _opener(body):
    """Кнопка целиком, вместе со значком: один регуляркой по `<button ...>` взялся
    бы только открывающий тег, и проверки значка смотрели бы мимо SVG."""
    found = re.search(r"<button[^>]*data-forecast-help-open[^>]*>.*?</button>", body, re.S)
    assert found, "у таблицы прогноза нет кнопки-вопроса"
    return found.group(0)


def _parent_div(body, position):
    """Индекс открывающего `<div>`, в котором лежит символ по индексу
    `position`, или `None`. Считается по вложенности в обе стороны, а не «последний
    открытый div перед ним»: у заголовка и у кнопки в одном ряду последним перед
    каждым будет один и тот же div, но при кнопке отдельной строкой —
    разные, и сравнить их и есть смысл."""
    stack = []
    for match in re.finditer(r"<div\b[^>]*>|</div>", body[:position]):
        if match.group(0) == "</div>":
            if stack:
                stack.pop()
        else:
            stack.append(match.start())
    return stack[-1] if stack else None


def _window_text(body):
    """Видимый текст окна без разметки. Режется по тегу, а не по границе
    страницы, иначе в текст попали бы атрибуты (`data-modal="forecast-help"`)
    и проверка «в окне нет слов кода» ругалась бы на собственную разметку."""
    start = body.index('<div class="app-modal" id="forecast-help-modal"')
    window = body[start:body.index("</main>")]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", window))


def _window_markup(body):
    """Разметка окна целиком. Ищется по своему `id`, а не по первому
    `app-modal` на странице: в шапке лежит ещё одно окно, то есть поиска, и
    первое совпадение было бы не тем."""
    start = body.index('<div class="app-modal" id="forecast-help-modal"')
    return body[start:body.index("</main>")]


def test_the_button_sits_next_to_the_heading_and_not_at_the_bottom(client, db_session):
    """Кнопка читается как относящаяся к таблице, а не как ещё один элемент
    страницы: заголовок и кнопка стоят в одной строке. Внизу под фильтром она
    выглядела бы частью фильтра."""
    _admin(client, db_session)
    body = client.get("/forecast").text
    heading = body.index("<h1")
    opener = body.index("data-forecast-help-open")
    table = body.index("<table")
    assert body[heading:].startswith("<h1 "), body[heading:heading + 60]
    assert heading < opener < table, (
        f"кнопка вне строки заголовка: заголовок {heading}, кнопка {opener}, таблица {table}")
    assert _parent_div(body, heading) is not None
    assert _parent_div(body, heading) == _parent_div(body, opener), (
        "кнопка стоит не в том же блоке, что заголовок, то есть отдельной строкой "
        f"под ним: {body[heading:heading + 200]!r}")


def test_the_button_carries_an_icon_and_a_real_name(client, db_session):
    """Значок один и тот же в шапке — обводкой текущим цветом, чтобы он брал
    цвет темы, и без имени для программы чтения: `aria-hidden` на SVG и
    `aria-label` на кнопке. Кнопка без подписи читалась бы как «изображение»."""
    _admin(client, db_session)
    opener = _opener(client.get("/forecast").text)
    assert 'aria-hidden="true"' in opener, opener
    assert 'stroke="currentColor"' in opener, (
        "значок залит своим цветом, а не текущим, и в тёмной теме поедет: " + opener)
    assert "<svg" in opener, opener
    label = re.search(r'aria-label="([^"]+)"', opener)
    assert label and label.group(1).strip(), "у кнопки-вопроса нет имени"
    shapes = re.findall(r"<(\w+)[^>]*/?>", opener[opener.index("<svg"):])
    assert shapes == ["svg", "circle", "path", "path"], (
        f"значок должен быть вопросительным знаком в круге, а он: {shapes}")
    assert 'r="8"' in opener and "circle" in opener, "в круге знака нет"


def test_the_button_says_what_it_opens(client, db_session):
    """Имя окна берётся из его заголовка, а не пишется дважды: переименование
    заголовка не оставило бы подпись кнопки старой."""
    _admin(client, db_session)
    body = client.get("/forecast").text
    opener = _opener(body)
    assert 'aria-haspopup="dialog"' in opener, opener
    assert 'aria-expanded="false"' in opener, opener
    controls = re.search(r'aria-controls="([^"]+)"', opener)
    assert controls, "кнопка не объявляет, какое окно открывает"
    assert f'id="{controls.group(1)}"' in body, (
        f"окна {controls.group(1)!r} на странице нет")
    panel = re.search(r"<div[^>]*class=\"app-modal-panel[^>]*>", _window_markup(body))
    assert panel, "панели окна нет"
    labelled = re.search(r'aria-labelledby="([^"]+)"', panel.group(0))
    assert labelled, "у окна нет имени"
    heading = re.search(r'<h2 id="' + labelled.group(1) + r'"[^>]*>([^<]+)</h2>', body)
    assert heading and heading.group(1).strip(), (
        f"окно ссылается на заголовок {labelled.group(1)!r}, которого нет")


def test_the_window_reuses_the_existing_modal_classes(client, db_session):
    """Второе окно того же понятия — это второй набор правил, который
    разъедется с первым при первом же изменении. Проверяется и разметка, и
    артефакт: класс, вычеркнутый из сборки, выпал бы молча."""
    _admin(client, db_session)
    window = _window_markup(client.get("/forecast").text)
    assert 'class="app-modal"' in window
    assert "app-modal-panel" in window
    source = TEMPLATE.read_text(encoding="utf-8")
    own = set(re.findall(r"app-modal[-a-z]*", source))
    assert own <= {"app-modal", "app-modal-panel"}, (
        f"страница завела свои классы окна вместо общих: {sorted(own)}")
    css = CSS.read_text(encoding="utf-8")
    for name in ("app-modal", "app-modal-panel"):
        rule = re.search(r"\.%s\{([^}]*)\}" % re.escape(name), css)
        assert rule and rule.group(1).strip(), f"{name} не доехал до сборки"


def _numeric_columns():
    """Подписи колонок, в которых живут числа, — взяты из самой страницы.

    Список не выдумывается здесь: он собирается из шапки таблицы прогноза и из
    класса ячейки в строке данных, потому что числа прижаты вправо, а название и
    артикул — нет. Добавление колонки с числом обязано ронять проверку, пока окно
    её не объяснит, и не выдумывать объяснение вслепую.
    """
    source = TEMPLATE.read_text(encoding="utf-8")
    heads = re.findall(r'sort_head\("([^"]+)"', source)
    row = re.search(r"\{%\s*for row in rows\s*%\}(.*?)\{%\s*endfor\s*%\}", source, re.S)
    assert row, "в шаблоне прогноза не найдена строка данных таблицы"
    cells = re.findall(r"<td([^>]*)>", row.group(1))
    assert len(cells) == len(heads), (
        f"в строке {len(cells)} ячеек, а в шапке {len(heads)} заголовков — проверка "
        "ослабла бы, если бы считала что-то одно")
    numeric = {index for index, attrs in enumerate(cells) if "text-right" in attrs}
    columns = [_text(heads[index]) for index in sorted(numeric)]
    assert len(columns) >= 6, f"числовых колонок найдено {len(columns)} — проверка ослабла"
    return columns


def _text(markup):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def test_the_window_explains_every_numeric_column_of_the_table(client, db_session):
    """Главная проверка окна: всё, что в таблице выглядит числом, там объяснено.

    Ищется не слово в тексте, а выделенное имя поля в списке «Поля таблицы»:
    проверка по вхождению слова проходила бы, даже если объяснение колонки
    убрали, а это слово осталось в формуле или в соседнем пункте, — так и
    случилось при первой попытке этой проверки, когда переименование «Запас» в
    «Скрытый запас» её не тронуло.

    Список колонок берётся из самой страницы, а не из фраз проверки: прежняя
    проверка искала восемь иголок в тексте и отвечала на вопрос «написано ли
    что-то похожее», а не «объяснена ли колонка». Теперь любая новая числовая
    колонка роняет прогон, пока о ней не написано в окне.
    """
    _admin(client, db_session)
    window = _window_markup(client.get("/forecast").text)
    fields = re.search(r"<ul[^>]*>(.*?)</ul>", window, re.S)
    assert fields, "в окне нет списка полей таблицы"
    named = [_text(part) for part in re.findall(r"<strong>(.*?)</strong>", fields.group(1), re.S)]
    assert len(named) >= 4, f"в списке полей {len(named)} выделенных имён — проверка ослабла"
    for column in _numeric_columns():
        assert any(column in name for name in named), (
            f"окно не объясняет колонку {column!r}: выделенных имён в списке полей {named}")


def test_the_window_names_every_horizon_the_service_forecasts(client, db_session):
    """Горизонты берутся из сервиса, а не из текста окна.

    Владелец не мог сопоставить колонки между собой именно потому, что их
    перечисляли словами. Если сервис начнёт считать, скажем, 60 дней, окно
    обязано заговорить о них же — иначе на странице появится колонка, которую
    никто не смог бы прочитать.
    """
    from app.modules.ai.service import FORECAST_HORIZONS

    _admin(client, db_session)
    text = _window_text(client.get("/forecast").text)
    for horizon in FORECAST_HORIZONS:
        assert str(horizon) in text, f"окно не называет горизонт {horizon} дней"


def test_the_window_does_not_say_a_horizon_is_a_daily_rate(client, db_session):
    """Самая частая ошибка при чтении таблицы: «30 дней» принять за «в сутки».

    В окне сказано об этом прямо, и это единственное место, где сказано: в
    справке о прогнозе — нет, а вторая копия разъехалась бы с первой при первом
    же изменении формулировки.
    """
    _admin(client, db_session)
    text = _window_text(client.get("/forecast").text)
    assert "количество за период" in text, "окно не разводит «за период» и «в сутки»"
    assert "в сутки»" in text, "окно не называет то, чем колонка быть не может"


def test_the_window_does_not_use_the_words_of_the_code(client, db_session):
    """Владелец спросил по-человечески; объяснение про `forecast_30` или
    `has_data` на русском языке отвечает не на его вопрос, а на свой."""
    _admin(client, db_session)
    text = _window_text(client.get("/forecast").text)
    for word in ("forecast", "has_data", "min_stock", "need", "None", "none"):
        assert word not in text, f"в окне мелькнул термин кода: {word!r}"


def test_the_window_says_the_limit_that_matters_in_practice(client, db_session):
    """Страница знает только остаток и продажи за 90 дней, поэтому может
    посоветовать купить то, что уже едет от поставщика. Это единственное
    ограничение, о котором человек узнает постфактум, и молчать о нём хуже,
    чем написать."""
    _admin(client, db_session)
    text = _window_text(client.get("/forecast").text)
    assert "90" in text, "окно не называет окно, из которого взяты продажи"
    assert "уже заказали" in text or "в пути" in text, (
        "окно не говорит, что о заказах поставщику приложение не знает")


def test_escape_closes_and_click_outside_closes_and_focus_returns(client, db_session):
    """Три способа закрыть — те же три, что у окна поиска, и тот же запрет
    ловушки фокуса: фокус возвращается на кнопку, поэтому с клавиатуры из
    окна всегда можно выйти и не нужно его запирать."""
    _admin(client, db_session)
    source = TEMPLATE.read_text(encoding="utf-8")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", source, re.S)
    ours = [block for block in blocks if "data-forecast-help-open" in block]
    assert len(ours) == 1, f"ожидался один скрипт окна, получено {len(ours)}"
    script = ours[0]
    assert 'event.key === "Escape"' in script, "Escape не закрывает окно"
    assert "event.target === modal" in script, "клик мимо панели не закрывает окно"
    assert "wasFocused" in script and "wasFocused.focus()" in script, (
        "фокус не возвращается на кнопку, и с клавиатуры из окна не выйти")
    assert "closeBtn.focus()" in script, (
        "при открытии фокус остаётся на кнопке под окном, и читать окно с "
        "клавиатуры нечем")


def test_the_modal_never_traps_focus():
    """Явная проверка на `preventDefault` и на Tab во всём скрипте: скрипт
    окна поиска и так их не слушает, и копия поведения сделала бы прогон
    зелёным при появлении ловушки."""
    source = TEMPLATE.read_text(encoding="utf-8")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", source, re.S)
    ours = [block for block in blocks if "data-forecast-help-open" in block]
    assert len(ours) == 1
    script = ours[0]
    assert "preventDefault" not in script
    assert '"Tab"' not in script and "'Tab'" not in script


def test_the_button_and_the_window_survive_an_empty_table(client, db_session):
    """Пустых данных на странице быть может (товары без учёта остатков), и
    кнопка обязана открываться всё равно: объяснение нужно и когда считать
    нечего."""
    _admin(client, db_session)
    r = client.get("/forecast")
    assert r.status_code == 200
    assert "data-forecast-help-open" in r.text
    assert "Товаров: 0" in r.text
