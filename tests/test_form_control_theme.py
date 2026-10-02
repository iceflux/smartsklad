"""Формы в тёмной теме: фон и рамка элементов управления.

`tests/test_design_tokens.py` сверяет ЗНАЧЕНИЯ токенов и пары «текст на фоне»,
но не знает, какой элемент каким токеном красится. Пробел, из-за которого дефект
и разошёлся: `:root`/`--color-primary` в тёмной теме верны, а поле входа всё
равно белое, потому что `background-color` для `input` не задаёт ни одно правило.
Пара «primary на surface-raised» в таблице контраста уже есть, и она зелёная —
проверка честно отвечает на вопрос «эта пара проходит?», а на вопрос «применена
ли она к полю?» отвечать не умеет. Этот файл отвечает на второй вопрос.

Проверка идёт по СОБРАННОМУ app/static/app.css, а не по исходнику: браузер грузит
артефакт, и молчаливая гибель правила при сборке видна только там. Правило ищется
по списку селекторов, а не по имени класса: классов у него нет, и проверка «есть
ли `.app-…`» такой случай пропускает молча.

Селекторы `button,input,optgroup,select,textarea{…color:inherit…}` из префлайта
подходят под тот же список и содержат `color`, поэтому проверка требует, чтобы
НАЙДЕННОЕ правило объявляло нужное свойство нужным токеном, и берёт из всех
подходящих правил то, где это выполнено. Иначе проверка прошлала бы на
префлайте, а поле осталось бы белым.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILT_CSS = ROOT / "app" / "static" / "app.css"
SOURCE_CSS = ROOT / "app" / "static" / "tailwind.css"

CONTROLS = ("input", "select", "textarea")
OPTION = "option"
SCHEME = re.compile(r"^\s*(light|dark|normal)\s*$")
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
TOKEN_VALUE = re.compile(r"rgb\(\s*var\(\s*--color-[a-z-]+\s*\)(?:\s*/[^)]*)?\s*\)")


def _built():
    return BUILT_CSS.read_text(encoding="utf-8")


def _source():
    return SOURCE_CSS.read_text(encoding="utf-8")


def _print_css():
    """Собранный CSS начиная с блока @media print с палитрой токенов.

    Отрезать по ПЕРВОМУ вхождению `@media print` нельзя: сборщик выводит ещё
    один, служебный (`.print\\:hidden`), и если бы он оказался раньше, отрезок
    «экран» тихо удлинился бы или укоротился, а проверки экрана стали бы
    проверками пустоты. Поэтому берём именно тот блок, где печатаются токены:
    следующий после первого `@media print`, если он содержит `--color-`.
    """
    built = _built()
    first = built.find("@media print")
    assert first != -1, "в app.css нет @media print"
    after = built[first + 1 :]
    for match in re.finditer(r"@media print\{", after):
        body = after[match.end() : after.index("}", match.end())]
        if "--color-" in body:
            return after[match.start() :]
    return after


def _screen_rules(selector_names):
    return _rules_from(_screen_css(), selector_names)


def _scheme_in(selector_names):
    """Значение `color-scheme` из ЭКРАННОГО правила — непустое и не `normal`.

    Пустое значение и `normal` прошли бы проверку «правило упомянуло
    color-scheme», не задав ничего: `normal` — это ровно то состояние, из-за
    которого глиф календаря остался чёрным на тёмном поле.
    """
    found = []
    for body in _screen_rules(selector_names):
        value = _declarations(body).get("color-scheme")
        if value and SCHEME.fullmatch(value) and value != "normal":
            found.append(value)
    return found


def test_color_scheme_is_set_on_both_schemes_so_native_widgets_follow_the_theme():
    # Три предыдущих теста этого файла читают app.css КАК ТЕКСТ и не рисуют ни
    # одного пикселя. Именно поэтому регрессия с глифом их прошла: `color-scheme`
    # не токен и в правиле полей не участвует — нативный виджет рисует его сам.
    # Проверка ловит отсутствие объявления; того, что виджет реально
    # перекрасился, она не видит и видеть не может (см. замер в отчёте).
    for selector in (":root", ".dark"):
        assert _scheme_in((selector,)), (
            f"{selector} не задаёт color-scheme: нативные виджеты (глиф календаря, "
            f"стрелки числа) рисуются палитрой схемы normal и на тёмном поле "
            f"почти сливаются с фоном"
        )


def test_color_scheme_differs_between_the_light_and_the_dark_scheme():
    # Без `light` в :root зеркальная ошибка: светлое поле рисует виджеты
    # тёмной палитрой. Значение, совпавшее с тёмным, — ровно тот же дефект.
    light, dark = _scheme_in((":root",)), _scheme_in((".dark",))
    assert light[0] != dark[0], "обе схемы получили одно значение color-scheme"


def test_print_forces_the_light_color_scheme_so_the_dark_one_does_not_reach_paper():
    # Проверено рендером в PDF: без этого `color-scheme: dark` из .dark доходит
    # до печати и Chrome заливает страницу чёрным вместо --color-surface.
    #
    # Проверяется АРТЕФАКТ, а не исходник. Читавшая исходник версия была слепа к
    # устаревшему или правившемуся руками app.css: удаление объявления из
    # собранного блока печати при нетронутом исходнике давало 7 passed, то есть
    # чёрная бумага могла уехать в коммит при зелёном прогоне.
    print_half = _print_css()
    values = _scheme_in_from(print_half, (":root", ".dark"))
    assert values, (
        "в собранном @media print нет color-scheme: тёмная схема утекает на бумагу "
        "и страница печатается залитой чёрным"
    )
    assert all(value == "light" for value in values), (
        f"в печати схема не светлая: {values}"
    )


def _scheme_in_from(text, selector_names):
    found = []
    for body in _rules_from(COMMENT.sub(" ", text), selector_names):
        value = _declarations(body).get("color-scheme")
        if value and SCHEME.fullmatch(value):
            found.append(value)
    return found


def _screen_css():
    """Собранный CSS до печатного блока с токенами.

    Отрезается по НАЧАЛУ найденного печатного блока, а не по первому
    `@media print`: служебный блок `.print\\:hidden` может прийти раньше, и
    отрезок по первому вхождению тогда не совпал бы с этим — проверки экрана
    стали бы проверками другой половины файла.

    Блок печати объявляет `:root, .dark` одним списком селекторов, поэтому он
    удовлетворяет проверку ЛЮБОГО из этих имён: тест «`.dark` задаёт color-scheme»
    проходил на объявлении из блока печати, а тест «`:root` задаёт» — тем более.
    Первая версия этих тестов была зелёной при удалённом `color-scheme: light` из
    `:root` именно поэтому. Экранная часть проверяется отдельно от печатной.
    """
    built = _built()
    print_at = built.find(_print_css())
    return built if print_at == -1 else built[:print_at]


def _rules_from(text, selector_names):
    found = []
    for selectors, body in RULE.findall(text):
        names = {part.strip() for part in selectors.split(",")}
        if all(name in names for name in selector_names):
            found.append(body)
    return found


def test_color_scheme_survives_the_build_inside_its_rule():
    # Отдельно от прочих: сборщик однажды вынес `color-scheme: light` из тела
    # :root наружу декларацией без селектора — она не досталась ни одному
    # элементу, и в app.css значение было, а на странице схемы не было. Проверка
    # по тексту артефакта ловит именно эту форму поломки.
    built = _built()
    for value in _scheme_in((":root",)) + _scheme_in((".dark",)):
        assert f"color-scheme:{value}" in built.replace(" ", ""), (
            f"color-scheme:{value} не доехал до app.css"
        )
    # Подсчёт сам по себе проверку не закрывал: он требовал лишь равенства числа
    # вхождений числу правил с color-scheme внутри, и на файле, где экранные
    # объявления на месте, этого хватало, чтобы пропустить выброшенную наружу
    # декларацию в блоке печати. Поэтому требуем по ИМЕНИ: каждое из трёх
    # обязательных правил должно нести объявление внутри своей скобки, а не
    # где-то ещё в файле.
    for label, selector_names in (
        ("экранный :root", (":root",)),
        ("экранный .dark", (".dark",)),
        ("блок @media print", (":root", ".dark")),
    ):
        region = _screen_css() if label.startswith("экранный") else _print_css()
        found = [
            value
            for body in _rules_from(region, selector_names)
            if (value := _declarations(body).get("color-scheme"))
            and SCHEME.fullmatch(value)
        ]
        assert found, (
            f"{label} не несёт color-scheme ВНУТРИ своего тела: сборщик выносит "
            f"такое объявление наружу декларацией без селектора, и оно не "
            f"достаётся ни одному элементу"
        )


def _rules(selector_names):
    """Все правила собранного CSS, чей список селекторов содержит все имена.

    Селекторы у сборщика склеены запятыми без пробелов, у префлайта тоже, но
    правило в исходнике может быть написано с пробелами — сравнение по
    разобранному списку, а не по подстроке, иначе `input[type=checkbox]`
    сошёл бы за `input`.
    """
    found = []
    for selectors, body in RULE.findall(_built()):
        names = {part.strip() for part in selectors.split(",")}
        if all(name in names for name in selector_names):
            found.append(body)
    return found


def _declarations(body):
    out = {}
    for piece in body.split(";"):
        if ":" in piece:
            prop, _, value = piece.partition(":")
            out[prop.strip()] = value.strip()
    return out


def _declaration_from_tokens(selector_names, prop, token):
    """Значение свойства из токена — и непустое, и действительно токеновое.

    `rgb(…)` обязателен: пустое значение, `inherit` или литерал прошли бы проверку
    «правило упомянуло --color-…», не задав ничего.
    """
    values = []
    for body in _rules(selector_names):
        value = _declarations(body).get(prop)
        if value and TOKEN_VALUE.fullmatch(value) and f"--color-{token}" in value:
            values.append(value)
    assert values, (
        f"{'/'.join(selector_names)} не красит `{prop}` через --color-{token}: "
        f"правило не доехало до app.css, объявление пустое или это не токен "
        f"(найдено правил: {len(_rules(selector_names))})"
    )
    return values[0]


def test_the_screen_half_is_cut_before_the_token_print_block_not_a_later_one():
    # Страховка на отрезок. Tailwind выводит служебный `@media print` для
    # `.print\:hidden`; сегодня он идёт ПОСЛЕ блока с токенами, и потому срез
    # по первому вхождению случайно попадал туда же. Если служебный блок
    # окажется раньше, срез по первому вхождению тихо отрежет другую половину
    # файла, и проверки экрана станут проверками пустоты — зелёными и
    # ничего не проверяющими. Здесь это закреплено явно.
    built = _built()
    print_half = _print_css()
    # _print_css() отдаёт хвост, начинающийся с `media print{…` (без `@`), поэтому
    # ищем начало на минус один символ: иначе индекс уезжает на байт и блок
    # сравнивается сам с собой.
    token_print = built.find("@" + print_half)
    assert token_print != -1, "печатный отрезок не найден в app.css"
    # Блок с токенами — тот, где реально печатается палитра; служебный
    # (`.print\:hidden`) токенов не содержит и в срез не попадает.
    service = [
        m.start()
        for m in re.finditer(r"@media print\{", built)
        if m.start() != token_print
    ]
    earlier = [at for at in service if at < token_print]
    assert not earlier or all(
        "--color-" not in built[at : built.index("}", at)] for at in earlier
    ), (
        "служебный @media print стоит раньше блока с токенами: срез экрана надо "
        "искать по содержимому, а не по первому вхождению"
    )
    # и экранная половина непуста и содержит правила, о которых идёт речь
    screen = _screen_css()
    assert ":root{" in screen and ".dark{" in screen, (
        "экранный отрезок не содержит :root/.dark — проверки экрана проверяют пустоту"
    )
    assert _scheme_in((":root",)) and _scheme_in((".dark",)), (
        "экранный отрезок не содержит color-scheme у :root/.dark"
    )


def test_form_controls_paint_their_surface_and_text_from_the_tokens():
    for names, label in ((CONTROLS, "input/select/textarea"), (CONTROLS + (OPTION,), "с option")):
        assert _declaration_from_tokens(names, "background-color", "surface-raised"), label
        assert _declaration_from_tokens(names, "color", "primary"), label


def test_form_controls_border_colour_comes_from_a_token_and_not_from_preflight():
    # Префлайт задаёт `border:0 solid #e5e7eb`, то есть СВЕТЛОЕ значение
    # --color-line, в обеих темах. Отсюда рамка поля в тёмной теме остаётся
    # светло-серой. Ловится здесь, а не на глаз: значение префлайта #e5e7eb
    # совпадает с токеном, и «светлый оттенок» в правиле не спрячешь.
    # Сокращение, а не `border-top-color`: правило объявляет именно `border-color`,
    # и проверка на длинную запись искала бы свойство, которого в нём нет и не
    # должно быть. Все четыре стороны сокращение покрывает — проверено замером
    # в браузере (см. отчёт).
    assert _declaration_from_tokens(CONTROLS, "border-color", "line"), (
        "рамка полей осталась литералом префлайта"
    )


def test_placeholders_are_dimmed_by_a_token_rather_than_by_a_preflight_literal():
    # Префлайт красит плейсхолдер #9ca3af — это светлое значение --color-subtle,
    # и в тёмной теме он остаётся тем же числом. Контраст тут не роняется,
    # но плейсхолдер перестаёт быть частью палитры: правка токена до него не
    # доедет. Проверяется токеном, а не «контрастом выше порога», потому что
    # контраст у этой пары и до правки был в порядке.
    for selector in ("input::placeholder", "textarea::placeholder"):
        assert _declaration_from_tokens((selector,), "color", "subtle"), selector


def test_the_checkbox_is_grown_vertically_centred_and_painted_from_a_token():
    """Чекбокс — нативный виджет, и у Tailwind v3 НЕТ утилиты `size-*`.

    Проверяется ровно то, чем это лечится, и все три свойства обязательны.
    Размер: `width` и `height` равны и равны 1.25rem — примерно половина высоты
    обычного поля `border p-2` (около 2.6rem), чего и просил владелец; число
    закреплено, потому что «примерно половину» в CSS не проверить, а уменьшить
    чекбокс обратно к 13px браузера — это откат, который иначе никто не заметит.
    Значение 0 не считается размером (проверено мутацией: правило с нулевым
    радиусом пережило бы проверку «свойство есть»).
    Выравнивание: `vertical-align: middle`, иначе у инлайнового чекбокса низ
    садится на базовую линию текста и он висит ниже подписи. `accent-color` —
    токен, а не литерал: в обеих темах отметка приходит из палитры, и заодно
    это то самое средство, которым в проекте лечат светлую рамку неотмеченного
    чекбокса в тёмной теме (открытый пункт из отчёта задачи 1).
    """
    css = _built()
    rule = _rule_body(css, r"input\[type=checkbox\]")
    assert rule, "в app.css нет правила для input[type=checkbox]"

    declared = dict(
        (prop.strip(), value.strip())
        for prop, _, value in (d.partition(":") for d in rule.split(";"))
        if prop.strip()
    )
    width, height = declared.get("width"), declared.get("height")
    assert width == height and width == "1.25rem", (
        f"чекбокс должен быть 1.25rem на 1.25rem, а не {width} на {height}"
    )
    assert declared.get("vertical-align") == "middle", (
        f"чекбокс не выровнен по середине строки: {declared.get('vertical-align')}"
    )
    assert "var(--color-accent)" in declared.get("accent-color", ""), (
        f"отметка чекбокса не на токене акцента: {declared.get('accent-color')}"
    )


def _rule_body(css, selector_pattern):
    match = re.search(selector_pattern + r"\s*\{([^}]*)\}", css)
    return match.group(1).strip() if match else ""