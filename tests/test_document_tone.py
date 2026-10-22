"""Сторож: документы не смягчают то, что человек делает руками.

Промт оформления запрещает «просто», «легко», «всего лишь» — но не потому, что
слово плохое, а потому что им прячут объём работы. Вход после импорта 1С
требует вбить остатки по каждому товару вручную, и фраза «просто внесите
остатки» делает ручной труд бесплатным на вид.

Проверка по слову здесь не годится: в наших документах «просто» стоит в
другом смысле — «данных о продажах просто мало», «адрес `/` просто переводит
на заказы». Это утверждение о факте, и запрещать его незачем. Поэтому ловится
не слово, а конструкция «слово + действие, которое делает человек»:
читателю обещают, что он сделает что-то легко, — а именно это и есть
смягчение, которое запрещено.

Аналогично ловится и запрещённое смягчение дефекта («небольшая неточность»):
оно опаснее прямого признания, потому что читатель решает, что чинить нечего.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "system_docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "user_guide.md",
    ROOT / "docs" / "admin_guide.md",
    ROOT / "docs" / "test_checklist.md",
]

# Слово + действие читателя. «просто нажмите», «просто введите» — читателю
# обещают лёгкость того, что на самом деле требует усилий.
SOFT_ACTION = re.compile(
    r"(просто|легко|всего лишь|всего-навсего)"
    r"\s*[—:,\-–]?\s*"
    r"(нажм\w+|введ\w+|впис\w+|внес\w+|вноси\w+|запиш\w+|создай\w*|"
    r"удал\w+|откр\w+|закр\w+|перезапуст\w+|обнов\w+|заполн\w+|"
    r"простав\w+|сдела\w*|постав\w+|добав\w+|измен\w+|настро\w+|"
    r"перейд\w+|открой\w*|запуст\w+|скопируй\w*|скача\w+|"
    r"воспользуйт\w+|достаточно|нажмите|выбер\w+|отметьт\w+|"
    r"включит\w+|выключит\w+|подтверд\w+|сохрани\w+|привяж\w+|"
    r"наберите|вбейте|забейте)",
    re.IGNORECASE,
)

# Смягчение дефекта: запрещённое «небольшая неточность» и подобное.
DEFECT_SOFTENING = re.compile(
    r"(небольшая\s+неточность|небольшой\s+(?:недочёт|баг|дефект)|"
    r"незначительн\w+\s+(?:ошибк|дефект|неточность|ошибка)|"
    r"почти\s+не\s+работает|почти\s+готов)",
    re.IGNORECASE,
)


def _prose_lines(path: Path):
    """Строки текста без блоков кода и без markdown-разметки строк списка.

    Внутри fence живут команды, и «просто запустите» там — это подсказка
    в командной строке, а не обещание читателю. Заголовки пропускаются: они
    не несут утверждения о труде.
    """
    inside = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside or not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        yield n, line


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_document_promises_the_reader_that_something_is_easy(path):
    hits = [
        (n, m.group(0))
        for n, line in _prose_lines(path)
        for m in [SOFT_ACTION.search(line)]
        if m
    ]
    assert not hits, (
        f"{path.name}: обещание лёгкости действия, которое делает человек — "
        f"промт запрещает «просто»/«легко» именно в этом смысле: {hits}"
    )


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_document_softens_a_defect(path):
    hits = [
        (n, m.group(0))
        for n, line in _prose_lines(path)
        for m in [DEFECT_SOFTENING.search(line)]
        if m
    ]
    assert not hits, (
        f"{path.name}: дефект назван мелким словом — по промту это хуже, "
        f"чем признать его прямо: {hits}"
    )


def test_the_guard_would_catch_the_wording_it_is_meant_to_catch():
    """Проверка проверки: она обязана ловить то, ради чего написана.

    Сторож, который ни на чём не падает, хуже отсутствия сторожа: он даёт
    зелёный прогон рядом с настоящим нарушением. Поэтому фразы, которые
    промт запрещает, проверяются прямо здесь, а не только в документах.
    """
    forbidden = [
        "Чтобы внести остатки, просто откройте карточку товара.",
        "Включить тему легко: нажмите кнопку в шапке.",
        "Всего лишь заполните поле и сохраните.",
    ]
    for phrase in forbidden:
        assert SOFT_ACTION.search(phrase), f"сторож пропустил: {phrase!r}"

    allowed = [
        "Данных о продажах просто мало, прогноз показывает «мало данных».",
        "Адрес `/` просто переводит на список заказов.",
        "ORM — sync SQLAlchemy 2.0.",
        "Правило простое: заполненное поле перекрывает `.env`.",
    ]
    for phrase in allowed:
        assert not SOFT_ACTION.search(phrase), f"сторож зря ругается: {phrase!r}"

    for phrase in ("Это небольшая неточность в расчёте.",
                   "Модуль почти готов, но не работает."):
        assert DEFECT_SOFTENING.search(phrase), f"сторож пропустил: {phrase!r}"


def test_the_orm_choice_states_a_reason_and_not_a_dismissal():
    """Причина выбора ORM проверяема, а «просто для MVP» — не причина.

    Замена sync-ORM на async — большой проход по коду, и решение пересматривают
    именно потому, что оно не обосновано. Значит обоснование должно быть видно
    и проверяемо, а не состоять из отмахивания.
    """
    notes = (ROOT / "system_docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    line = next(l for l in notes.splitlines() if "SQLAlchemy 2.0" in l)
    assert "run_in_threadpool" in line, line
    assert "просто для MVP" not in notes, (
        "«просто для MVP» — отмахивание, а не обоснование"
    )