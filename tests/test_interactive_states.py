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

ROW_HOVER_RULE = r"tbody tr:hover\{([^}]*)\}"

"""Фоны, на которых реально лежит строка таблицы. `surface` — тело страницы,
`surface-raised` — приподнятая карточка (дашборд, аудит), `surface-sunken` —
мягко удалённая строка, и последний стоит в разметке классом-утилитой."""
ROW_BACKGROUNDS = ("surface", "surface-raised", "surface-sunken")

"""Классы состояний, за которыми стоит кнопка. Скругление добавляется к ним
же, а не отдельным классом в разметке: отдельный класс означал бы второе
место, где сказано «кнопка круглая», и правку в каждом шаблоне при смене
радиуса. Четыре класса покрывают все 86 кнопок проекта."""
BUTTON_STATE_CLASSES = ("app-btn", "app-btn-accent", "app-btn-line", "app-btn-danger")
"""Базовые (без :hover) правила тех же четырёх классов: скругление живёт в них,
а наведение его не повторяет, поэтому нужен именно этот селектор, а не тот,
что уже проверяет состояние."""
BUTTON_BASE_RULES = {
    "app-btn": r"\.app-btn\{([^}]*)\}",
    "app-btn-accent": r"\.app-btn-accent\{([^}]*)\}",
    "app-btn-line": r"\.app-btn-line\{([^}]*)\}",
    "app-btn-danger": r"\.app-btn-danger\{([^}]*)\}",
}

"""Свойства, которые цвета не несут: подчёркивание у ссылки, затемнение у
акцентной кнопки и скругление у кнопки. Всё остальное обязано быть на токене —
иначе состояние поедет в тёмной теме, где значения токенов другие."""
NON_COLOUR_DECLARATIONS = {"text-decoration", "filter", "border-radius"}

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
    assert found == {"a": 98, "button": 75}, (
        f"интерактивных элементов {found}, а проверка ждёт 98 ссылок и 75 кнопок: "
        "она обязана падать, когда появится новая, ещё не описанная здесь. "
        "Пять ссылок стояли на удалённой главной странице index.html, "
        "ссылка на товар — на странице поиска, кнопка «Найти» — в его форме в шапке, "
        "а на странице заказа добавились ссылка «Скачать PDF» и две кнопки отправки. "
        "Кнопок стало на две меньше, когда строка позиции заказа съехала в макрос "
        "order_form.html: раньше кнопка «Удалить» лежала в исходнике трижды — в ветке "
        "с существующими позициями, в ветке без них и в <template> добавляемой строки, "
        "а теперь макрос вызывается и даёт ту же кнопку на каждую отрисованную строку. "
        "Ещё пять кнопок добавились у строки заказа 04.10: «Выбрать товар» и "
        "«Создать товар» в макросе подстановки (по одной в исходнике, на каждую "
        "отрисованную строку), «Закрыть» в каждом из двух окон и «Создать» в форме "
        "нового товара. Потом ещё пять: те же «Выбрать клиента» и «Создать "
        "клиента» в макросе подстановки и по одной «Закрыть» и «Создать» в двух "
        "клиентских окнах. 05.10 кнопок стало на две больше: поиск из раскрывающегося "
        "<details> стал кнопкой-лупой в шапке и отдельным окном с кнопкой «Закрыть», а "
        "форма внутри окна получила свою «Найти» — ровно та же, что была в шапке. "
        "Ссылки не сдвинулись: фильтр клиентов уехал в `partials/`, и одна ссылка "
        "«Сбросить» снята со страницы и появилась в партиале — то есть на странице "
        "клиентов её по-прежнему ровно одна. "
        "Ссылка на сортировку в шапке таблицы в счёт не идёт целиком: она лежит "
        "в macros.html один раз и разворачивается в 12 ссылок на трёх страницах "
        "разделов, и сколько их на самом деле, проверяет tests/test_list_sorting.py. "
        "Пункты окон выбора в счёт не идут: их рисует скрипт, в исходнике "
        "шаблона их нет, и проверяет их tests/test_order_picker_visibility.py. "
        "05.10 ссылок стало на две больше у страницы заказа: печать одним кликом "
        "и предпросмотр печати. Обе ссылки ведут на `/orders/{id}/print` и обе "
        "несли бы нажатие в предпросмотр, но обработчик в `base.html` ловит "
        "только `[data-print-now]`: у этой ссылки метка есть, у следующей нет, "
        "и предпросмотр остаётся доступным как ссылка. "
        "06.10 кнопок стало на две больше: кнопка-вопрос у заголовка таблицы "
        "прогноза и «Закрыть» в её окне — те же два, что у окна поиска в шапке, "
        "потому что окно то же самое. 07.10 ссылок стало на 35 больше: 34 у страницы "
        "справки и одна в окне прогноза — на раздел справки о прогнозе. "
        "После переписывания справки в справочник ссылок стало на 39 вместо 34: "
        "оглавление выросло с 17 якорей до 19 (добавились «Как пользоваться справкой», "
        "«Ограничения MVP» и «Известные проблемы» вместо одного «Чего пока нет»), "
        "новый блок в начале ведёт на «Роли», а две ссылки внутри текста ведут на "
        "раздел «Что отправляется». Кнопок не прибавилось: на справке их нет вовсе. "
        "Позже ссылка стало на одну больше: пункт про минимальный запас ведёт на "
        "«Настройки», где задаётся «Запас в днях». Сами девятнадцать якорей оглавления "
        "переехали с класса состояния ссылки на класс состояния кнопки — рамку, "
        "скругление и наведение даёт `app-btn` в одном правиле, и второго класса "
        "состояния на ссылке быть не может, иначе каскад перестаёт читаться. "
        "Ссылок стало на одну больше: в разделе справки о ролях отвечается на "
        "вопрос «почему раздела нет в меню», и рядом с ответом стоит ссылка на "
        "страницу, где право выдают."
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


def _over(foreground, background, alpha):
    """Затемнённый или осветлённый цвет токена, сведённый к непрозрачному.

    Фон наведения задан как `rgb(var(--color-accent) / 0.12)`, то есть
    полупрозрачным, а WCAG считает по непрозрачному цвету: считать пару
    «полупрозрачный токен против токена» нельзя, а свести её можно ровно
    одним сложением, и это то, что делает браузер.
    """
    top = [int(part) for part in foreground.split()]
    under = [int(part) for part in background.split()]
    return " ".join(str(round(top[i] * alpha + under[i] * (1 - alpha))) for i in range(3))


def test_every_button_is_rounded_by_its_state_class_rather_than_by_markup():
    """Скругление живёт в классах состояний, а не в разметке.

    Класс-утилита `rounded` в разметке означала бы, что радиус назван дважды —
    здесь и в CSS — и что новую кнопку легко забыть скруглить. Поэтому
    проверка двусторонняя: каждый из четырёх классов кнопки несёт радиус в
    собранном CSS, и ни одна кнопка в разметке радиус не повторяет.
    """
    css = _built()
    missing = []
    for name in BUTTON_STATE_CLASSES:
        radius = re.search(r"border-radius:\s*([^;}]+)", _body(css, BUTTON_BASE_RULES[name]))
        if radius is None:
            missing.append(f"{name}: скругления нет вовсе")
        elif float(radius.group(1).rstrip("rem")) == 0:
            missing.append(f"{name}: скругление нулевое, кнопка снова квадратная")
    assert missing == [], f"{missing}"

    repeated = [f"{path.relative_to(ROOT)}: {tag[:80]}" for path, tag, _markup, classes
                in _interactive() if "rounded" in classes]
    assert repeated == [], (
        "скругление повторено в разметке, а оно уже есть в классе состояния: "
        f"{repeated}"
    )


def test_a_table_row_reacts_on_hover_in_both_themes():
    """Строка таблицы обязана реагировать на курсор, иначе по длинному списку
    нельзя понять, на какую строку нажали.

    Проверяется три вещи. Правило доехало до артефакта непустым и лежит на
    токене — иначе оно поедет за палитрой в тёмной теме. Контраст текста на
    подложке наведения считается для всех трёх фонов, на которых строка
    реально лежит, в обеих темах: полупрозрачный акцент сведён к
    непрозрачному цвету тем же сложением, что делает браузер. И правило
    побеждает утилиту `bg-surface-sunken`, которой помечена мягко удалённая
    строка: утилита весит (0,1,0), а `tbody tr:hover` — (0,0,2), поэтому без
    `!important` наведение на удалённых строках было бы мёртвым, и заметить
    это, глядя на страницу, нельзя.
    """
    body = _body(_built(), ROW_HOVER_RULE).strip()
    declaration = re.search(r"background-color:([^;]+)", body)
    assert declaration, f"у наведения строки нет фона: {body}"
    alpha = re.search(r"var\(--color-accent\)\s*/\s*([\d.]+)\)", declaration.group(1))
    assert alpha, f"фон наведения не на токене акцента: {declaration.group(1)}"
    assert "!important" in body, (
        "без !important утилита bg-surface-sunken мягко удалённой строки "
        f"(0,1,0) перебьёт этот селектор (0,0,2), и наведение на ней умрёт: {body}"
    )

    weight = float(alpha.group(1))
    measured = {}
    for theme, palette in (("светлая", _light()), ("тёмная", _dark())):
        for name in ROW_BACKGROUNDS:
            washed = _over(palette["accent"], palette[name], weight)
            measured[f"{name} в {theme} теме"] = round(
                _contrast({"ink": palette["primary"], "fill": washed}, "ink", "fill"),
                2,
            )
    assert {label: value for label, value in measured.items() if value < MIN_NORMAL_TEXT} == {}, (
        f"текст строки ниже {MIN_NORMAL_TEXT} под фоном наведения: {measured}"
    )