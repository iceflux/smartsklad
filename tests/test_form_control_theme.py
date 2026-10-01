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

CONTROLS = ("input", "select", "textarea")
OPTION = "option"

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
TOKEN_VALUE = re.compile(r"rgb\(\s*var\(\s*--color-[a-z-]+\s*\)(?:\s*/[^)]*)?\s*\)")


def _built():
    return BUILT_CSS.read_text(encoding="utf-8")


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