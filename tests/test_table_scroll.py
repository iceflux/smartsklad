import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
CSS = TEMPLATES.parent / "static" / "app.css"
SCROLL_WRAPPER = '<div class="overflow-x-auto">'
SCROLL_CLASS = "overflow-x-auto"
PRINT_DIR = "documents"
TABLE_RE = re.compile(r"<table\b")
SCREEN_TABLE_COUNT = 21


def _tables(lines):
    return [i for i, line in enumerate(lines) if TABLE_RE.search(line)]


def _screen_templates():
    return [
        path
        for path in sorted(TEMPLATES.rglob("*.html"))
        if PRINT_DIR not in path.relative_to(TEMPLATES).parts
    ]


def _unwrapped(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    found = []
    for index in _tables(lines):
        opens = lines[index - 1].strip() if index else ""
        end = next(
            (i for i in range(index + 1, len(lines)) if "</table>" in lines[i]),
            None,
        )
        closes = lines[end + 1].strip() if end is not None and end + 1 < len(lines) else ""
        if opens != SCROLL_WRAPPER or closes != "</div>":
            found.append(f"{path.relative_to(TEMPLATES)}:{index + 1}")
    return found


def test_every_screen_table_sits_in_a_horizontal_scroll_wrapper():
    unwrapped = [name for path in _screen_templates() for name in _unwrapped(path)]
    assert not unwrapped, "tables outside a scroll wrapper: " + ", ".join(unwrapped)


def test_the_screen_table_count_is_the_one_the_inventory_recorded():
    total = sum(len(_tables(path.read_text(encoding="utf-8").splitlines())) for path in _screen_templates())
    assert total == SCREEN_TABLE_COUNT, f"screen tables: {total}, recorded {SCREEN_TABLE_COUNT}"


def test_no_table_is_wrapped_in_a_second_scroll_wrapper():
    doubled = [
        path.relative_to(TEMPLATES).as_posix()
        for path in _screen_templates()
        if path.read_text(encoding="utf-8").count(SCROLL_WRAPPER)
        != len(_tables(path.read_text(encoding="utf-8").splitlines()))
    ]
    assert not doubled, "wrapper count differs from table count: " + ", ".join(doubled)


def test_the_print_document_keeps_no_scroll_wrapper():
    text = (TEMPLATES / PRINT_DIR / "order_print.html").read_text(encoding="utf-8")
    assert SCROLL_CLASS not in text
    assert text.count("<table") == 1


def test_the_scroll_class_reaches_the_shipped_stylesheet():
    css = CSS.read_text(encoding="utf-8").replace(" ", "")
    assert f".{SCROLL_CLASS}{{overflow-x:auto}}" in css
