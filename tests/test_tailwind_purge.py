import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
BUILT_CSS = ROOT / "app" / "static" / "app.css"

"""`NOT_STYLED_BY_TAILWIND` holds only bare js hooks, never a tailwind utility.
If one of these names ever gains a real utility next to the querySelectorAll,
delete it from the mapping so the purge test starts checking it again.

`LEGACY_SHADES` — тот список, ради которого этот файл и менялся. Проверка
«класс есть в собранном CSS» литеральный оттенок пропускает: `text-blue-600`
собрался бы и выглядел бы правильно, просто мимо токена, и в тёмной теме остался
бы синим. Поэтому возврат оттенка ловится отдельно и падает всегда."""

NOT_STYLED_BY_TAILWIND = {
    "delete-category": "js hook, document.querySelectorAll('.delete-category') in categories.html",
    "role-form": "js hook, document.querySelectorAll('.role-form') in users.html",
}

CLASS_ATTR = re.compile(r"""class=(["'])(.*?)\1""", re.DOTALL)

SVG_LITERAL_COLOUR = re.compile(
    r"\b(?:fill|stroke|stop-color|flood-color|lighting-color)\s*=\s*([\"'])([^\"']*)\1"
    r"|\bstyle\s*=\s*([\"'])([^\"']*)\3",
    re.IGNORECASE,
)

CSS_PAINT_PROPERTY = re.compile(
    r"(?:^|[;{\s])(?:fill|stroke|color|stop-color|flood-color|lighting-color)\s*:\s*([^;}]*)",
    re.IGNORECASE,
)

# Ключевые слова CSS, которые в графике набирают руками. `none`, `currentColor`,
# `inherit`, `transparent` и `url(#…)` цветами не считаются: ими рисуют контур
# иконок в самих шаблонах, запрещать их нельзя.
NAMED_COLOURS = (
    "aqua|black|blue|brown|coral|crimson|cyan|darkblue|darkgray|darkgreen|darkgrey|"
    "darkred|fuchsia|gold|gray|green|grey|indigo|ivory|khaki|lime|linen|magenta|"
    "maroon|navy|olive|orange|orchid|peru|pink|plum|purple|red|salmon|sienna|"
    "silver|teal|tomato|turquoise|violet|wheat|white|yellow"
)

HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
FUNC_COLOUR = re.compile(r"(?:rgb|rgba|hsl|hsla)\(", re.IGNORECASE)
NAMED_COLOUR = re.compile(f"^(?:{NAMED_COLOURS})$", re.IGNORECASE)
NOT_A_COLOUR = frozenset({"", "none", "currentcolor", "inherit", "transparent", "unset"})


def _is_literal_colour(value: str) -> bool:
    value = value.strip()
    if value.lower() in NOT_A_COLOUR or value.lower().startswith("url("):
        return False
    return bool(HEX_COLOUR.fullmatch(value)
                or FUNC_COLOUR.match(value)
                or NAMED_COLOUR.match(value))

# Пусто: печатный шаблон `documents/order_print.html` своего SVG не содержит
# (проверено), печать всегда светлая, и исключать там нечего. Список пустой, а
# не с именем «на всякий случай»: имя в allowlist скрывает ровно то, ради чего
# проверка затевалась.
ALLOWED_SVG_LITERAL_COLOUR = frozenset()

LEGACY_SHADES = re.compile(
    r"^(?:bg|text|border|ring|divide|placeholder|from|to|via|outline|decoration"
    r"|accent|caret|fill|stroke|shadow)-"
    r"(?:(?:gray|blue|green|red|amber|yellow|violet)-(?:50|100|200|300|400|500|600|700|800|900)"
    r"|white|black)$"
)

MENU_SELECTORS = [
    r'body\[data-nav=("?)open\1\] \.app-sidebar',
    r'body\[data-nav=("?)closed\1\] \.app-overlay',
    r"\.app-sidebar\{",
    r"\.app-overlay\{",
    r"\.app-nav-current\{",
    # Десктопное переопределение — отдельный селектор, а не продолжение базового
    # правила: без него на мониторе остаётся `position:fixed` +
    # `translateX(-100%)`, бургер скрыт (`lg:hidden`) и ни один из 14 разделов
    # недостижим. Проверка «`.app-sidebar{` есть в CSS» этот шов не видит: строка
    # в списке уже стоит, а панель всё равно не на экране.
    r"@media \(min-width:1024px\)\{\.app-sidebar\{[^}]*position:sticky",
    # Свёрнутое по настройке состояние живёт здесь же, на широком экране.
    r'body\[data-sidebar=("?)collapsed\1\] \.app-sidebar\{',
    # Закрытая панель обязана быть `visibility: hidden`: трансформация не
    # вынимает ссылки из порядка табуляции.
    r"\.app-sidebar\{[^}]*visibility:hidden",
    r'body\[data-nav=("?)open\1\] \.app-sidebar\{[^}]*visibility:visible',
]


def _class_attrs():
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in CLASS_ATTR.finditer(text):
            yield path, match.group(2)


def _classes_in_templates():
    found = {}
    for path, value in _class_attrs():
        for name in re.sub(r"{%.*?%}", " ", value).split():
            found.setdefault(name, path.relative_to(ROOT))
    return found


def _class_selector(name):
    css_escaped = re.sub(r"([^\w-])", r"\\\1", name)
    return re.compile(r"\." + re.escape(css_escaped) + r"(?![-\w\\])")


def test_every_template_class_is_in_the_built_stylesheet():
    css = BUILT_CSS.read_text(encoding="utf-8")
    missing = {
        name: str(where)
        for name, where in _classes_in_templates().items()
        if name not in NOT_STYLED_BY_TAILWIND
        and not _class_selector(name).search(css)
    }
    assert missing == {}


def test_no_template_uses_a_literal_palette_shade():
    found = {
        name: str(where)
        for name, where in _classes_in_templates().items()
        if LEGACY_SHADES.match(name)
    }
    assert found == {}, (
        "в разметке вернулся литеральный оттенок Tailwind вместо токена: "
        f"{found}. Соответствие «оттенок → токен» перечислено в system_docs/ARCHITECTURE.md."
    )


def test_no_svg_presentation_attribute_carries_a_literal_colour():
    """Литеральный цвет в разметке рисунка запрещён: у него нет тёмного соответствия.

    `class` сторож выше по цвету не видит, а `var()` в атрибут презентации не
    подставляется — отсюда и был захардкоженный hex графика. Выход — класс
    `fill-*`/`stroke-*`: приоритет авторского правила выше атрибута, а сборщик
    выдаёт всю семью утилит из `theme.extend.colors`.

    Ловится всё, чем цвет реально можно задать: hex (обе кавычки), `rgb()`/
    `hsl()`, именованный цвет из `NAMED_COLOURS` и `style="fill:…;stroke:…"`.
    Не ловится и не должен ловиться `fill="none"` и `stroke="currentColor"` —
    ими нарисованы иконки в самих шаблонах. Список имёнованных цветов закрытый:
    `slategray` в него не входит, и такой цвет пройдёт мимо проверки.
    """
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name in ALLOWED_SVG_LITERAL_COLOUR:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in SVG_LITERAL_COLOUR.finditer(line):
                values = ([match.group(2)] if match.group(2) is not None
                          else CSS_PAINT_PROPERTY.findall(match.group(4)))
                for value in values:
                    if _is_literal_colour(value):
                        found.append(f"{path.relative_to(ROOT)}:{n} {match.group(0)[:90]}")
    assert found == [], (
        "литеральный цвет в разметке рисунка: "
        + "; ".join(found)
        + ". Цветом рисунка должен быть класс fill-*/stroke-* по токену."
    )


def test_no_template_composes_a_class_name_from_a_variable():
    for path, value in _class_attrs():
        assert "{{" not in value, f"{path.name}: {value}"


def test_templates_do_not_load_the_tailwind_play_cdn():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "cdn.tailwindcss.com" not in base
    assert '/static/app.css' in base


def test_the_current_item_looks_different_from_a_hovered_one():
    """Текущий пункт и наведение обязаны различаться, иначе «где я» не видно.

    Следующий шаг дописывает сюда состояния фокуса, поэтому проверка сравнивает
    текущее правило с уже существующим :hover, а не с их полным набором.
    """
    css = BUILT_CSS.read_text(encoding="utf-8")
    current = re.search(r"\.app-nav-current\{([^}]*)\}", css).group(1)
    hover = re.search(r"\.app-nav-link:hover\{([^}]*)\}", css).group(1)
    assert current != hover, "текущий пункт и наведение выглядят одинаково"
    assert "var(--color-" in current, "правило текущего пункта обязано быть на токенах"


def test_the_shipped_css_carries_the_selectors_the_menu_markup_and_script_depend_on():
    """Шов между разметкой, скриптом и CSS.

    node-харнесс подставляет телу документа свой собственный `data-nav="closed"`,
    поэтому сверки с серверной разметкой он не делает и переименование селектора
    в CSS проходит мимо него зелёным. Чистая проверка «класс есть в собранном
    CSS» тем же свойством обладает: `.app-sidebar` в разметке есть, `.app-drawer`
    в CSS тоже есть, а правило, которое им связывает, никто не ищет. Минификатор
    CSS-склеивает селекторы, поэтому кавычки в значении атрибута в артефакте
    может не быть — ищется любая из двух форм. Сверяется список: переименование
    только в CSS роняет эту проверку, а разметка и скрипт остаются нетронутыми.
    """
    css = BUILT_CSS.read_text(encoding="utf-8")
    missing = [selector for selector in MENU_SELECTORS
               if not re.search(selector, css)]
    assert missing == [], f"в app.css нет селекторов, на которых держится меню: {missing}"
