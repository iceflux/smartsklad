"""Сетки с фиксированным числом колонок.

Инвентаризация: `grid-cols-N` без префикса — это не мелочь, а застывший
макет, который на 375 пикселях даёт каждой ячейке треть ширины. Проверка из
двух частей, и обе нужны: список исключений требует, чтобы новая фиксированная
сетка попала в перечень с причиной, а последняя проверка закрывает печатный
документ, где адаптив не нужен и вреден.

`NARROW_GRID_ALLOWLIST` — три списка «термин/значение» в широких карточках и
один на дашборде. Складывать их нельзя: подпись и её значение идут в паре, и
на 375 пикселях подпись («Позиций склеено», «Следующая копия») ещё помещается
в ~150 пикселей, а стек растянул бы страницу вдвое ради ничего. Формы и
карточки метрик исключениями не являются — их как раз и надо было стековать.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
PRINT_DOCUMENT = "documents/order_print.html"

CLASS_ATTR = re.compile(r"""class=(["'])(.*?)\1""", re.DOTALL)
FIXED_COLUMNS = re.compile(r"^grid-cols-(\d+)$")
PREFIXED_COLUMNS = re.compile(r"^(?:sm|md|lg|xl):grid-cols-(\d+)$")
UNPREFIXED_SPAN = re.compile(r"^col-span-(\d+)$")

NARROW_GRID_ALLOWLIST = {
    "backups.html": [
        "dl Последняя проверка / Планировщик — пары подпись-значение в широкой карточке",
        "dl pg_dump / Папка доступна для записи — пары подпись-значение в широкой карточке",
    ],
    "import.html": [
        "dl Товаров создать / ИНН отброшено — пары подпись-число в широкой карточке",
    ],
}


def _class_attrs():
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in CLASS_ATTR.finditer(text):
            yield path, match.group(2)


def _names(path, value):
    return re.sub(r"{%.*?%}", " ", value).split()


def _fixed_grids():
    """Многоколоночные (не одна) сетки, ключ — путь шаблона относительно
    `app/templates`."""
    counted = {}
    for path, value in _class_attrs():
        found = [
            n
            for n in _names(path, value)
            if (m := FIXED_COLUMNS.match(n)) and int(m.group(1)) > 1
        ]
        if found:
            name = path.relative_to(TEMPLATES).as_posix()
            counted.setdefault(name, []).extend(found)
    return counted


def test_every_fixed_column_grid_is_excepted_with_a_recorded_reason():
    found = _fixed_grids()
    unexcepted = {
        name: columns for name, columns in found.items() if name not in NARROW_GRID_ALLOWLIST
    }
    assert unexcepted == {}, (
        "фиксированная сетка без записи в исключениях: "
        f"{unexcepted}. Причина, почему её не стековать, пишется в NARROW_GRID_ALLOWLIST."
    )


def test_the_inventory_of_fixed_grids_matches_what_the_allowlist_records():
    expected = {name: len(reasons) for name, reasons in NARROW_GRID_ALLOWLIST.items()}
    found = {name: len(columns) for name, columns in _fixed_grids().items()}
    assert found == expected, f"сетки: {found}, записано: {expected}"


def test_a_grid_that_stacks_keeps_one_column_on_a_phone():
    """Обратная сторона инвентаризации: у раскладывающейся сетки базовая
    колонка ровно одна. `grid-cols-2 md:grid-cols-3` формально не закреплён
    первыми двумя проверками, но на телефоне остаётся двухколоночным — это тот
    же дефект, который задача снимает, только на другой сетке."""
    wrong = []
    for path, value in _class_attrs():
        names = _names(path, value)
        columns = [int(FIXED_COLUMNS.match(n).group(1)) for n in names if FIXED_COLUMNS.match(n)]
        prefixed = [
            int(PREFIXED_COLUMNS.match(n).group(1)) for n in names if PREFIXED_COLUMNS.match(n)
        ]
        if prefixed and columns and 1 not in columns:
            wrong.append(f"{path.name}: {columns} -> {prefixed}")
    assert not wrong, "сетка остаётся многоколоночной на телефоне: " + ", ".join(wrong)


def test_no_column_span_outlives_the_columns_it_spans():
    """`col-span-2` рядом с сеткой, у которой на телефоне одна колонка,
    создаёт неявную вторую колонку шириной под содержимое — на экране 375
    это уводит контейнер за край. Перекрытие поэтому всегда с префиксом."""
    found = [
        path.relative_to(TEMPLATES).as_posix()
        for path, value in _class_attrs()
        for name in _names(path, value)
        if UNPREFIXED_SPAN.match(name)
    ]
    assert not found, "col-span без префикса рядом со складывающейся сеткой: " + ", ".join(found)


def test_the_print_document_carries_no_responsive_layout():
    text = (TEMPLATES / PRINT_DOCUMENT).read_text(encoding="utf-8")
    prefixed = re.findall(r"(?:^|\s)(?:sm|md|lg|xl):[\w-]+", text)
    assert prefixed == [], f"в печатном документе появился адаптив: {prefixed}"
    assert "grid-cols-" not in text