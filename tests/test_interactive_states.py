"""Состояния интерактивных элементов: наведение мышью и видимый фокус с клавиатуры.

Правило живёт в разметке, состояние — в `app/static/tailwind.css`, поэтому
проверяется две вещи: (1) что ни одна ссылка и ни одна кнопка не осталась без
состояния, и (2) что состояния доехали до собранного `app.css` непустыми
правилами. Второе — не формальность: сборщик вычищает `@layer components` по
вхождению класса в шаблонах, и имя, разъехавшееся между разметкой и CSS,
выпадает молча, ровно как пустой селектор.

Фокус нарисован собственным кольцом, а не оставлен браузеру: обводка
пользовательского агента одинакова для всех элементов и на акцентной заливке
кнопки не видна. Проверка утверждает, что кольцо составлено из обводки и канта,
и что нигде в артефакте обводка не подавлена.

Контраст считается для фонов, которые меняет наведение: такая пара в разметке
не встречается статически, и без расчёта «кликабельная кнопка с текстом 2:1» —
это дефект, который не видно глазом. Порог тот же, что в test_design_tokens, —
4.5 для обычного текста, и обе темы: фон меняется на токене, у токена есть
тёмное значение.
"""

import re
from pathlib import Path

from tests.test_design_tokens import MIN_NORMAL_TEXT, _contrast, _dark, _light

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
BUILT_CSS = ROOT / "app" / "static" / "app.css"

"""Одно состояние на элемент: две строки с состояниями означают, что какое-то
из них перебивает другое каскадом, и читать такой класс невозможно."""
STATE_CLASSES = {
    "app-link",
    "app-btn",
    "app-btn-accent",
    "app-btn-line",
    "app-btn-danger",
    "app-nav-link",
}

STATE_RULES = {
    "app-link": r"\.app-link:hover\{([^}]*)\}",
    "app-btn": r"\.app-btn:hover\{([^}]*)\}",
    "app-btn-line": r"\.app-btn-line:hover\{([^}]*)\}",
    "app-btn-danger": r"\.app-btn-danger:hover\{([^}]*)\}",
    "app-btn-accent": r"\.app-btn-accent:hover\{([^}]*)\}",
    "app-nav-link": r"\.app-nav-link:hover\{([^}]*)\}",
}

FOCUS_RULE = r":where\(a,button,input,select,textarea,summary\):focus-visible\{([^}]*)\}"

"""Свойства, которые цвета не несут: подчёркивание у ссылки и затемнение у
акцентной кнопки. Всё остальное обязано быть на токене — иначе состояние
поедет в тёмной теме, где значения токенов другие."""
NON_COLOUR_DECLARATIONS = {"text-decoration", "filter"}

TAG = re.compile(r"<(a|button)\b([^>]*)>", re.S)
CLASS = re.compile(r"""class=(["'])(.*?)\1""", re.S)
JINJA = re.compile(r"{%.*?%}")
VARIANT = re.compile(r"^(?:hover|focus|focus-visible|focus-within|active):")

"""Пары «текст на фоне наведения»: (текст, фон) берутся из правил состояний
выше, а не из разметки — в разметке такой пары статически нет."""
HOVER_PAIRS = {
    ".app-btn:hover": ("primary", "surface-sunken"),
    ".app-btn-line:hover": ("primary", "line-strong"),
    ".app-nav-link:hover": ("primary", "surface-sunken"),
    "кнопка с text-accent под app-btn": ("accent", "surface-sunken"),
}


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def _interactive():
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for match in TAG.finditer(text):
            found = CLASS.search(match.group(2))
            yield (
                path,
                match.group(1),
                match.group(0),
                set(JINJA.sub(" ", found.group(2)).split()) if found else set(),
            )


def _built():
    return BUILT_CSS.read_text(encoding="utf-8")


def _body(css, pattern):
    match = re.search(pattern, css)
    assert match, f"в app.css нет правила {pattern}"
    return match.group(1)


def _channels(palette, name):
    return [int(part) for part in palette[name].split()]


def _scaled(channels, factor):
    return [min(255, max(0, round(part * factor))) for part in channels]


def _pairs(palette, extra):
    measured = {}
    for label, (text, background) in HOVER_PAIRS.items():
        measured[label] = round(_contrast(palette, text, background), 2)
    measured.update(extra(palette))
    return {label: ratio for label, ratio in measured.items() if ratio < MIN_NORMAL_TEXT}


def test_every_link_and_button_carries_exactly_one_state_class():
    found = {}
    for path, tag_name, tag, classes in _interactive():
        states = classes & STATE_CLASSES
        assert len(states) == 1, (
            f"{path.relative_to(ROOT)}: {tag[:110]} — состояний {sorted(states) or 'нет'}"
        )
        found[tag_name] = found.get(tag_name, 0) + 1
    assert found == {"a": 54, "button": 61}, (
        f"интерактивных элементов {found}, а проверка ждёт 54 ссылки и 61 кнопку: "
        "она обязана падать, когда появится новая, ещё не описанная здесь. "
        "Пять ссылок стояли на удалённой главной странице index.html, "
        "ссылка на товар — на странице поиска, кнопка «Найти» — в его форме в шапке. "
        "Раскрывающийся поиск сделан на <summary>, а он не входит в TAG: "
        "его состояние проверяет tests/test_search.py."
    )


def test_no_template_states_anything_with_a_hover_or_focus_variant():
    found = {}
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for match in CLASS.finditer(text):
            for name in JINJA.sub(" ", match.group(2)).split():
                if VARIANT.match(name):
                    found.setdefault(name, path.relative_to(ROOT))
    assert found == {}, (
        f"состояние задано классом-вариантом в разметке: {sorted(found)}. "
        "Состояние живёт в tailwind.css, иначе его придётся повторять везде."
    )


def test_every_state_rule_reaches_the_built_stylesheet_non_empty():
    css = _built()
    missing = {}
    for name, pattern in STATE_RULES.items():
        body = _body(css, pattern).strip()
        if not body:
            missing[name] = "правило есть, но пустое"
            continue
        for declaration in body.split(";"):
            prop, _sep, value = declaration.partition(":")
            if prop in NON_COLOUR_DECLARATIONS:
                continue
            if "var(--color-" not in value:
                missing[name] = f"{prop} не на токене: {declaration}"
                break
    assert missing == {}, missing


def test_focus_is_our_own_ring_rather_than_the_browser_default():
    body = _body(_built(), FOCUS_RULE)
    outline = re.search(r"outline:[^;]+", body)
    assert outline, f"у состояния фокуса нет обводки: {body}"
    assert "var(--color-" in outline.group(0), (
        f"обводка фокуса не на токене и поедет в тёмной теме: {outline.group(0)}"
    )
    assert "outline-offset" in body and "box-shadow" in body, (
        f"без канта кольцо не отличить от заливки акцентной кнопки: {body}"
    )
    suppressed = re.findall(r"outline:(?:none|0[;\s])", _built())
    assert suppressed == [], f"обводка фокуса подавлена в собранном CSS: {suppressed[:5]}"


def test_focus_is_a_third_appearance_next_to_current_and_hover():
    css = _built()
    current = _body(css, r"\.app-nav-current\{([^}]*)\}")
    hover = _body(css, STATE_RULES["app-nav-link"])
    focus = _body(css, FOCUS_RULE)
    assert current != hover
    assert "background-color" not in focus and "filter" not in focus, (
        f"фокус меняет вид так же, как наведение, и спутать их нельзя: {focus}"
    )
    assert hover not in focus and current not in focus


def test_hover_states_stay_readable_in_both_themes():
    css = _built()
    factor = float(re.search(r"filter:brightness\(([\d.]+)\)", _body(css, STATE_RULES["app-btn-accent"])).group(1))

    def accent_button(palette):
        # filter:brightness масштабирует и заливку, и текст на ней — считаем оба.
        text = _scaled(_channels(palette, "on-accent"), factor)
        background = _scaled(_channels(palette, "accent"), factor)
        scaled = {"ink": " ".join(str(part) for part in text),
                  "fill": " ".join(str(part) for part in background)}
        one, two = _luminance(scaled["ink"]), _luminance(scaled["fill"])
        return {".app-btn-accent:hover": round((max(one, two) + 0.05) / (min(one, two) + 0.05), 2)}

    for name, palette in (("светлая", _light()), ("тёмная", _dark())):
        assert _pairs(palette, accent_button) == {}, (
            f"наведение оставляет текст ниже {MIN_NORMAL_TEXT} в {name} теме"
        )


def test_no_hover_paints_a_background_under_the_danger_text():
    """Под красным текстом в светлой теме нет проходного фона темнее белого.

    Проверяется не результат расчёта (его делает
    test_hover_states_stay_readable_in_both_themes), а сам запрет: любая заливка
    под danger даёт меньше 4.5, и заметить это, глядя на страницу, нельзя —
    кнопка выглядит просто «нажатой на вид».
    """
    body = _body(_built(), STATE_RULES["app-btn-danger"])
    assert "background" not in body, (
        f"наведение подложило фон под текст danger, где он нечитаем: {body}"
    )


def _luminance(channels):
    parts = []
    for raw in channels.split():
        value = int(raw) / 255
        parts.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]