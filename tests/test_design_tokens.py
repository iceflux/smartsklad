"""Стоп-guard семантических токенов: замена оттенков Tailwind на классы токенов
обязана оставить вид приложения тем же. Пять проверок: светлая палитра равна
прежним оттенкам, тёмная отличается от светлой, контраст посчитан по WCAG, печать
остаётся светлой, каждый зарегистрированный токен доехал до собранного CSS.

Порог контраста один, 4.5 для обычного текста. Второй порог WCAG — 3.0 для
крупного (>=24px либо >=18.66px полужирного) — в таблице не используется ни для
одной пары, и это не пропуск: в шаблонах нет ни одной текстовой пары, которая
встречалась бы только крупным шрифтом. Заголовки h1-h3 наследуют цвет body
(primary) на фоне страницы (surface), и та же пара стоит в тексте 12px, так что
честный порог для неё 4.5, а 3.0 был бы проверкой, которая не может упасть.

Пары взяты из реальной вёрстки, а не из всех 23x7 комбинаций. Исключены две,
которые в разметке не стоят рядом ни разу: `subtle` на приглушённой строке
(roles.html:32 — «нет прав» лежит на белой строке, плашки bg-gray-100 там в
другой ветке) и `danger` на приглушённой строке (красного текста в удалённых
строках нет ни разу). Обе числились провалами в таблице Task 1; судить о палитре
по сочетаниям, которых не бывает, нечего.

Печать проверяется в двух местах намеренно. Порядок в ИСХОДНИКЕ — наша ответственность:
блок @media print обязан стоять после .dark и вне @layer base, потому что правило
без слоя приходит в каскад позже любого правила из слоя. Порядок в СОБРАННОМ CSS —
гарантия сборщика: измерено, что Tailwind 3.4.19 поднимает неслойный @media print
в конец вывода независимо от того, где блок стоял в tailwind.css, и даже если его
засунуть внутрь @layer base. Поэтому перестановка блока в исходнике проверяется
без пересборки, а в артефакте проверяется только то, что блок туда доехал.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_CSS = ROOT / "app" / "static" / "tailwind.css"
BUILT_CSS = ROOT / "app" / "static" / "app.css"
CONFIG_JS = ROOT / "tailwind.config.js"

MIN_NORMAL_TEXT = 4.5

LIGHT_SHADES = {
    "surface": ("gray-50", "f9fafb"),
    "surface-raised": ("white", "ffffff"),
    "surface-sunken": ("gray-100", "f3f4f6"),
    "line": ("gray-200", "e5e7eb"),
    "line-strong": ("gray-300", "d1d5db"),
    "primary": ("gray-900", "111827"),
    "secondary": ("gray-700", "374151"),
    "caption": ("gray-600", "4b5563"),
    "muted": ("gray-500", "6b7280"),
    "subtle": ("gray-400", "9ca3af"),
    "on-accent": ("white", "ffffff"),
    "accent": ("blue-600", "2563eb"),
    "danger": ("red-600", "dc2626"),
    "danger-bg": ("red-50", "fef2f2"),
    "danger-border": ("red-200", "fecaca"),
    "success": ("green-600", "16a34a"),
    "success-bg": ("green-50", "f0fdf4"),
    "success-border": ("green-200", "bbf7d0"),
    "warning": ("amber-600", "d97706"),
    "warning-bg": ("amber-50", "fffbeb"),
    "warning-border": ("amber-200", "fde68a"),
    "warning-strong-bg": ("amber-100", "fef3c7"),
    "warning-strong-border": ("amber-300", "fcd34d"),
}

PAIRS = [
    ("primary", "surface"),
    ("primary", "surface-raised"),
    ("primary", "surface-sunken"),
    ("primary", "danger-bg"),
    ("primary", "success-bg"),
    ("primary", "warning-bg"),
    ("primary", "warning-strong-bg"),
    ("secondary", "surface"),
    ("secondary", "surface-raised"),
    ("caption", "surface-raised"),
    ("caption", "surface-sunken"),
    ("muted", "surface-raised"),
    ("muted", "surface-sunken"),
    ("subtle", "surface-raised"),
    ("on-accent", "accent"),
    ("accent", "surface"),
    ("accent", "surface-raised"),
    ("accent", "surface-sunken"),
    ("danger", "surface"),
    ("danger", "surface-raised"),
    ("success", "surface-raised"),
    ("success", "success-bg"),
    ("warning", "surface-raised"),
    ("warning", "warning-bg"),
    ("warning", "warning-strong-bg"),
]

LIGHT_EXEMPTIONS = {
    ("subtle", "surface-raised"): (2.54, "text-gray-400 «нет прав», roles.html:32"),
    ("muted", "surface-sunken"): (4.39, "text-gray-500 на bg-gray-100 удалённых строк"),
    ("success", "surface-raised"): (3.30, "text-green-600 на белом, 4 места"),
    ("success", "success-bg"): (3.15, "то же значение на зелёной плашке"),
    ("warning", "surface-raised"): (3.19, "text-amber-600, в шаблонах не используется"),
    ("warning", "warning-bg"): (3.07, "то же значение на своей плашке"),
    ("warning", "warning-strong-bg"): (2.86, "то же значение на баннере техработ"),
}

PRINT_RULE = re.compile(r"@media\s+print\s*\{")
DECLARATION = re.compile(r"--color-([a-z-]+)\s*:\s*([^;}]+)\s*;?")
QUOTED = re.compile(r'"([a-z][a-z-]*)"')


def _source():
    return SOURCE_CSS.read_text(encoding="utf-8")


def _built():
    return BUILT_CSS.read_text(encoding="utf-8")


def _head_and_print():
    source = _source()
    found = PRINT_RULE.search(source)
    assert found, "в tailwind.css нет @media print: тёмная тема дойдёт до бумаги"
    return source[: found.start()], source[found.end() :]


def _rule(text, selector):
    match = re.search(re.escape(selector) + r"\s*\{", text)
    assert match, f"{selector} не найден"
    return match


def _values(text, selector):
    at = _rule(text, selector).end()
    return dict(DECLARATION.findall(text[at : text.index("}", at)]))


def _light():
    return _values(_head_and_print()[0], ":root")


def _dark():
    return _values(_head_and_print()[0], ".dark")


def _registered():
    body = CONFIG_JS.read_text(encoding="utf-8").split("const colors")[1]
    return set(QUOTED.findall(body.split("]")[0]))


def _hex(channels):
    return "".join(f"{int(part):02x}" for part in channels.split())


def _luminance(channels):
    parts = []
    for raw in channels.split():
        value = int(raw) / 255
        parts.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def _contrast(palette, text, background):
    one, two = _luminance(palette[text]), _luminance(palette[background])
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def _failing_pairs(palette):
    return {
        (text, background): _contrast(palette, text, background)
        for text, background in PAIRS
        if _contrast(palette, text, background) < MIN_NORMAL_TEXT
    }


def _depth_at(text, offset):
    level = 0
    for char in text[:offset]:
        if char == "{":
            level += 1
        elif char == "}":
            level -= 1
    return level


def test_light_values_are_exactly_the_legacy_shades_they_replace():
    light = _light()
    assert set(light) == set(LIGHT_SHADES), (
        f"в :root не те токены: лишние {sorted(set(light) - set(LIGHT_SHADES))}, "
        f"пропущенные {sorted(set(LIGHT_SHADES) - set(light))}"
    )
    wrong = {
        name: f"{light[name]!r} != #{hexcode} ({shade})"
        for name, (shade, hexcode) in LIGHT_SHADES.items()
        if _hex(light[name]) != hexcode
    }
    assert wrong == {}


def test_dark_differs_from_light_for_every_token():
    light, dark = _light(), _dark()
    assert set(dark) == set(light)
    same = sorted(name for name in light if dark[name] == light[name])
    assert same == [], f"в тёмной теме значение совпадает со светлым: {same}"


def test_dark_theme_text_contrast_meets_wcag_aa():
    measured = {pair: round(value, 2) for pair, value in _failing_pairs(_dark()).items()}
    assert measured == {}


def test_light_theme_text_contrast_fails_only_on_the_recorded_legacy_shades():
    measured = {pair: round(value, 2) for pair, value in _failing_pairs(_light()).items()}
    assert measured == {pair: ratio for pair, (ratio, _note) in LIGHT_EXEMPTIONS.items()}


def test_print_forces_the_light_palette_over_the_dark_one():
    source = _source()
    head, print_part = _head_and_print()
    dark_rule = _rule(head, ".dark")
    assert _depth_at(head, dark_rule.start()) == 0
    assert dark_rule.start() < source.index("@media print")
    printed = _values(print_part, ".dark")
    assert set(printed) == set(LIGHT_SHADES)
    wrong = {name: printed[name] for name in LIGHT_SHADES if _hex(printed[name]) != LIGHT_SHADES[name][1]}
    assert wrong == {}


def test_print_rules_outrank_the_dark_rules():
    built = _built()
    found = PRINT_RULE.search(built)
    assert found, "в app/static/app.css нет @media print: тёмная тема дойдёт до бумаги"
    assert _rule(built, ".dark").start() < found.start()
    forced = {name for name, _value in DECLARATION.findall(built[found.end() :])}
    assert forced >= set(LIGHT_SHADES)


def test_every_registered_token_reaches_the_built_stylesheet_with_a_non_empty_value():
    registered = _registered()
    assert registered == set(LIGHT_SHADES)
    assert set(_light()) == registered
    assert set(_dark()) == registered
    built = _built()
    emitted = {**_values(built, ":root"), **_values(built, ".dark")}
    assert set(emitted) == registered, (
        f"токен объявлен, но в app.css доехал с пустым значением или не доехал: "
        f"{sorted(set(registered) - set(emitted))}"
    )
    missing = {
        name
        for name in registered
        if not re.search(
            r"\.text-" + re.escape(name) + r"(?![-\w\\])\s*\{[^}]*var\(--color-" + re.escape(name) + r"\)",
            built,
        )
    }
    assert missing == set(), f"токен зарегистрирован, но класс в собранный CSS не попал: {sorted(missing)}"
