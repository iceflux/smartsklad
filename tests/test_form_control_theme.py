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


def _screen_css():
    """Собранный CSS без блока @media print.

    Блок печати объявляет `:root, .dark` одним списком селекторов, поэтому он
    удовлетворяет проверку ЛЮБОГО из этих имён: тест «`.dark` задаёт color-scheme»
    проходил на объявлении из блока печати, а тест «`:root` задаёт» — тем более.
    Первая версия этих тестов была зелёной при удалённом `color-scheme: light` из
    `:root` именно поэтому. Экранная часть проверяется отдельно от печатной.
    """
    built = _built()
    at = built.find("@media print")
    return built if at == -1 else built[:at]


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
    source = _source()
    print_block = source[source.index("@media print") :]
    assert _scheme_in_from(print_block, (":root", ".dark")), (
        "@media print не возвращает color-scheme: light: тёмная схема утекает на бумагу"
    )


def _scheme_in_from(text, selector_names):
    found = []
    for body in _rules_from(COMMENT.sub(" ", text), selector_names):
        value = _declarations(body).get("color-scheme")
        if value and SCHEME.fullmatch(value):
            found.append(value)
    return found


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
    stray = re.findall(r"[;}]\s*color-scheme\s*:", built)
    assert not stray or built.count("color-scheme") == len(
        re.findall(r"\{[^}]*color-scheme", built)
    ), "в app.css есть color-scheme вне тела правила — он ни к чему не применён"


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