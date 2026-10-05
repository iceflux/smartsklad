import re
import zlib
from pathlib import Path

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.service import create_order
from app.modules.printing.service import (
    build_order_context, render_order_html, render_order_pdf,
)
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


def _flate_streams(data: bytes) -> bytes:
    """Decompressed PDF streams — reportlab embeds the ToUnicode CMap here."""
    joined = b""
    pos = 0
    while True:
        s = data.find(b"stream\n", pos)
        if s == -1:
            return joined
        e = data.find(b"endstream", s)
        if e == -1:
            return joined
        try:
            joined += zlib.decompress(data[s + 7 : e].rstrip(b"\n\r"))
        except Exception:
            pass
        pos = e + 10


@pytest.fixture
def env(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=50)
    c = Customer(name="ООО Строй", email="a@b.ru")
    db_session.add_all([p, c])
    db_session.commit()
    o = create_order(db_session, customer_id=c.id, discount=10,
                     items=[{"product_id": p.id, "quantity": 2, "discount": 5}])
    return p, c, o


def test_build_order_context_rounds_and_sums(db_session, env):
    p, c, o = env
    ctx = build_order_context(o, db_session)
    assert ctx["number"] == o.number
    assert ctx["customer_name"] == "ООО Строй"
    assert ctx["discount"] == 10
    assert ctx["total_amount"] == o.total_amount
    item = ctx["items"][0]
    assert item["name"] == "Гвозди"
    assert item["quantity"] == 2
    assert item["sum"] == round(100.0 * (1 - 5 / 100.0) * 2, 2)  # 190.0


def test_build_order_context_tolerates_missing_customer(db_session, env):
    p, c, o = env
    c.is_deleted = True
    db_session.commit()
    ctx = build_order_context(o, db_session)
    assert ctx["customer_name"] == ""
    assert len(ctx["items"]) == 1


def test_render_order_html_contains_order_data(db_session, env):
    _, _, o = env
    html = render_order_html(o, db_session)
    assert "Заказ покупателя" in html
    assert o.number in html
    assert "Гвозди" in html


def test_render_order_pdf_valid_pdf(db_session, env):
    _, _, o = env
    data = render_order_pdf(o, db_session)
    assert data.startswith(b"%PDF")
    assert b"%%EOF" in data
    cmap = _flate_streams(data)
    assert b"begincmap" in cmap, "ToUnicode CMap missing — cyrillic glyphs lost"
    for ch in "Заказ":
        cp = f"{ord(ch):04X}".encode()
        assert b"<" + cp + b">" in cmap, f"cyrillic U+{ord(ch):04X} missing from ToUnicode CMap"


def test_render_order_html_escapes_customer_name(db_session, env):
    _, c, o = env
    c.name = "<script>alert(1)</script>"
    db_session.commit()
    html = render_order_html(o, db_session)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


PASSWORD = "password123"
BUILT_CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "app.css"
# Правила печати лежат в собранном `app.css`, а не в разметке страницы: состояние
# задаётся в `app/static/tailwind.css`, как и все прочие состояния. Читается
# артефакт, а не исходник — иначе правка исходника, до сборки которой дело не
# дошло бы, прошла бы проверку зелёной.
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
TAG = re.compile(r"<([a-z][a-z0-9]*)\b([^>]*)>", re.I)
CLASS_ATTR = re.compile(r"""class=(["'])(.*?)\1""", re.S)
SIMPLE_SELECTOR = re.compile(
    r"^(?:body\[(?P<mark>[^\]]+)\]\s+)?(?P<tag>[a-z][a-z0-9]*|\*)?(?P<classes>(?:\.[\w:\\.-]+)*)$"
)
BODY_MARK = re.compile(r'<body[^>]*data-print-document="([^"]*)"', re.I)


def _admin(client, db_session):
    user = User(username="boss", password_hash=hash_password(PASSWORD),
                role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(user)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _elements(html):
    """(имя тега, множество классов) для каждого тега в разметке."""
    found = []
    for match in TAG.finditer(html):
        classes = CLASS_ATTR.search(match.group(2))
        found.append((match.group(1).lower(),
                      set(classes.group(2).split()) if classes else set()))
    return found


def _unescape(name):
    """Имя класса из селектора в том виде, в каком оно лежит в атрибуте.

    Обратная косая черта в CSS экранирует следующий символ, и именно поэтому
    `.print\\:hidden` — это класс с ЛИТЕРАЛЬНОЙ обратной чертой внутри и ничего
    больше, то есть он не подходит к `print:hidden`. Схлопывать черты нельзя:
    селектор с удвоенной чертой прошёл бы проверку, не печатая ровно того, что
    она проверяет.
    """
    out = []
    index = 0
    while index < len(name):
        if name[index] == "\\" and index + 1 < len(name):
            out.append(name[index + 1])
            index += 2
        else:
            out.append(name[index])
            index += 1
    return "".join(out)


def _matches(element, selector, body_mark=None):
    """Подпадает ли элемент под селектор.

    Умеет имя тега, имена классов и метку на `<body>` — ровно столько, сколько
    нужно правилу печати. Селектор сложнее разбора НЕ пропускается молча:
    проверка падает и просит разбор расширить. Это и есть смысл проверки — не
    сравнить строку селектора с разметкой, а разрешить её: правило с `nav`
    вместо `.app-sidebar` строке в разметке отвечает, элементу — нет.

    Метка на `<body>` обязательна и проверяется по САМОЙ странице: правило
    `body[data-print-document] …` обязано прятать оболочку там, где метка стоит,
    и не обязано там, где её нет, — иначе Ctrl+P на любой странице печатал бы
    почти пустой лист.
    """
    parsed = SIMPLE_SELECTOR.match(selector)
    assert parsed, f"селектор печати не разбирается проверкой: {selector!r}"
    if parsed.group("mark") is not None and body_mark is None:
        return False
    tag, classes = element
    want_tag = parsed.group("tag")
    if want_tag not in (None, "*") and want_tag.lower() != tag:
        return False
    return all(_unescape(name) in classes
               for name in re.findall(r"\.([\w:\\.-]+)", parsed.group("classes") or ""))


def _media_print_blocks(text):
    """Тела печатных блоков, найденные по парности скобок.

    Прежний regexp с хвостом `\\s*$` тянул до ПОСЛЕДНЕЙ скобки файла, то есть
    в печатный блок попадали ещё и экранные правила, стоящие ниже: на сборке с
    печатью с страницы заказа так и вышло — экранное `display:none` области
    печати считалось печатным правилом, и проверка требовала на него мишень на
    предпросмотре, где её быть и не должно.
    """
    blocks = []
    for match in re.finditer(r"@media\s+print\s*\{", text):
        depth, index = 1, match.end()
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        blocks.append(text[match.end():index - 1])
    return blocks


def _print_hides():
    """Селекторы печатных правил, которые что-то ПРЯЧУТ.

    Отбираются по `display:none` в теле правила, а не все подряд: палитра печати
    тоже живёт в печатном блоке, и её селекторы (`:root`, `.dark`) к элементам
    страницы отношения не имеют — проверка разрешения селекторов на них
    заорала бы впустую.
    """
    rules = []
    for block in _media_print_blocks(BUILT_CSS.read_text(encoding="utf-8")):
        for selector, body in RULE.findall(block):
            if "display:none" not in body.replace(" ", ""):
                continue
            rules.extend(part.strip() for part in selector.split(","))
    return [rule for rule in rules if rule]


def _body_mark(html):
    found = BODY_MARK.search(html)
    return found.group(1) if found else None


def test_the_print_preview_prints_the_document_and_nothing_of_the_shell(client, db_session, env):
    """Ctrl+P на предпросмотре должен печатать только документ.

    Проверка разрешает каждый селектор правил печати из собранного CSS по
    разметке этой же страницы, а не сравнивает строки. Прежний селектор `nav`
    строке отвечал, элементу — нет: панель стала `<aside class="app-sidebar">`
    ещё в задаче про вертикальный сайдбар, и с тех пор печаталась она вместе с
    шапкой.
    """
    _admin(client, db_session)
    _, _, order = env
    response = client.get(f"/orders/{order.id}/print")
    assert response.status_code == 200
    html = response.text

    rules = _print_hides()
    assert rules, "в собранном CSS нет ни одного правила печати"
    mark = _body_mark(html)
    assert mark == "true", f"на предпросмотре печати нет метки на body: {mark!r}"

    elements = _elements(html)
    dead = [rule for rule in rules if not any(_matches(e, rule, mark) for e in elements)]
    assert dead == [], f"правила печати, которым не на что нападать: {dead}"

    shell = [e for e in elements
             if e[0] in ("header", "aside") or e[1] & {"app-sidebar", "app-overlay"}]
    assert shell, "в предпросмотре нет ни шапки, ни панели — проверять нечего"
    uncovered = [e for e in shell if not any(_matches(e, rule, mark) for rule in rules)]
    assert uncovered == [], f"эти части оболочки попадут на бумагу: {uncovered}"


def test_the_print_preview_hides_its_own_controls(client, db_session, env):
    """Ряд кнопок над документом не должен печататься.

    Отдельно от предыдущей проверки, потому что это другой селектор и другая
    причина: прежняя строка писала `.print\\\\:hidden` — с удвоенной обратной
    косой чертой, то есть буквальным символом `\\\\` в имени класса, которого на
    элементе нет. Скрывало кнопки правило Tailwind в `app.css`, то есть
    предпросмот печати зависел от того, что сборщик не вычистил класс.
    """
    _admin(client, db_session)
    _, _, order = env
    html = client.get(f"/orders/{order.id}/print").text
    mark = _body_mark(html)
    elements = _elements(html)
    hidden = [e for e in elements if any(_matches(e, rule, mark) for rule in _print_hides())]
    assert any("print:hidden" in e[1] for e in hidden), (
        "блок кнопок над документом не скрывается на печати"
    )