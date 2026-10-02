import base64
import re
import zlib

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name
from app.modules.orders.service import create_order
from app.modules.printing.service import build_order_context, render_order_html, render_order_pdf
from app.modules.settings.service import COMPANY_FIELDS, get_setting, set_setting

PASSWORD = "password123"
OBJECT = re.compile(rb"(\d+) 0 obj(.*?)endobj", re.S)
FONT = re.compile(rb"/Name\s*/([^\s/]+).*?/ToUnicode\s+(\d+) 0 R", re.S)
CONTENTS = re.compile(rb"/Contents\s+(\d+) 0 R")
CMAP_BLOCK = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
CMAP_PAIR = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
SET_FONT = re.compile(rb"/([^\s/]+)\s+[\d.]+\s+Tf")
SHOWN = re.compile(rb"\((?:[^()\\]|\\.)*\)\s*Tj")


def _objects(data: bytes):
    found = {}
    for match in OBJECT.finditer(data):
        body = match.group(2)
        if b"stream" in body:
            body = body[:body.find(b"stream")]
        found[int(match.group(1))] = body
    return found


def _stream(data: bytes, number: int) -> bytes:
    header = re.search(rb"(?<![0-9])%d 0 obj" % number, data)
    if header is None:
        return b""
    start = data.find(b"\n", data.find(b"stream", header.end())) + 1
    body = data[start:data.find(b"endstream", start)].strip(b"\r\n")
    for unpack in (lambda b: zlib.decompress(b),
                   lambda b: zlib.decompress(
                       base64.a85decode(b[:-2] if b.endswith(b"~>") else b))):
        try:
            return unpack(body)
        except Exception:
            continue
    return body


def _unpack(raw: bytes) -> str:
    raw = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8)]), raw)
    return re.sub(rb"\\([()\\])", rb"\1", raw).decode("latin-1")


def pdf_text(data: bytes) -> str:
    """Текст из готового PDF.

    ReportLab не пишет текст как есть: он подставляет шрифтовой поднабор и
    кодирует буквы номерами глифов, а читающий браузер раскладывает их обратно
    по таблице ToUnicode. Здесь ровно то же, и поштучно по шрифтам: на
    странице их два (обычный и жирный), у каждого своя нумерация глифов, и одна
    общая таблица на документ перепутала бы их — это проверка, которая не может
    упасть. Без такого чтения «продавец напечатан в PDF» было бы проверкой на глаз.
    """
    objects = _objects(data)
    fonts = {}
    for body in objects.values():
        named = FONT.search(body)
        if named:
            tables = {}
            for block in CMAP_BLOCK.findall(_stream(data, int(named.group(2)))):
                for code, uni in CMAP_PAIR.findall(block):
                    tables[int(code, 16)] = chr(int(uni, 16))
            fonts[named.group(1).decode()] = tables
    lines = []
    for body in objects.values():
        contents = CONTENTS.search(body)
        if contents is None:
            continue
        current: dict[int, str] = {}
        for token in re.finditer(rb"/([^\s/]+)\s+[\d.]+\s+Tf|\((?:[^()\\]|\\.)*\)\s*Tj",
                                 _stream(data, int(contents.group(1)))):
            if token.group(1):
                current = fonts.get(token.group(1).decode(), {})
                continue
            raw = _unpack(token.group(0)[1:token.group(0).rindex(b")")])
            lines.append("".join(current.get(ord(char), char) for char in raw))
    return "\n".join(lines)


def _admin(client, db_session):
    user = User(username="boss", password_hash=hash_password(PASSWORD),
                role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(user)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _order(db_session):
    product = Product(name="Гвозди", sku="NAIL-1", unit_id=get_unit_by_name(db_session, "шт").id,
                      price=100.0, rest=50)
    customer = Customer(name="ООО Строй", email="a@b.ru")
    db_session.add_all([product, customer])
    db_session.commit()
    return create_order(db_session, customer_id=customer.id, discount=0,
                        items=[{"product_id": product.id, "quantity": 2, "discount": 0}])


def _fill(db_session, **values):
    for key, value in values.items():
        set_setting(db_session, key, value, user=None)


FULL = {"company_name": "ООО Ромашка", "company_inn": "7701234567",
        "company_kpp": "770101001", "company_address": "Москва, ул. Лесная, 1",
        "company_phone": "+7 495 000-00-00", "company_email": "zakaz@romashka.ru"}


def test_the_settings_table_stores_the_company_as_keys_and_needs_no_migration():
    from app.modules.settings.models import Setting
    assert set(FULL) == {key for key, _label in COMPANY_FIELDS}
    columns = {column.name for column in Setting.__table__.columns}
    assert not columns & set(FULL), "поля компании стали колонками — значит нужна миграция"


def test_the_settings_page_offers_every_company_field(client, db_session):
    _admin(client, db_session)
    body = client.get("/settings").text
    for key, label in COMPANY_FIELDS:
        assert f'name="{key}"' in body, key
        assert label in body, label


def test_the_settings_page_saves_the_company_and_the_audit_says_so(client, db_session):
    _admin(client, db_session)
    data = {"track_rest_default": "true", "user_password_charset": "ABC123",
            "user_password_length": "12", "min_stock_days": "14"}
    data.update(FULL)
    response = client.post("/settings", data=data, follow_redirects=False)
    assert response.status_code == 303
    for key, value in FULL.items():
        assert get_setting(db_session, key) == value, key
    written = {row.new_value for row in db_session.query(AuditLog).all()}
    assert set(FULL.values()) <= written


def test_the_settings_page_shows_saved_company_values_back(client, db_session):
    _admin(client, db_session)
    _fill(db_session, company_name="ООО Ромашка")
    body = client.get("/settings").text
    assert 'value="ООО Ромашка"' in body


def test_the_seller_block_reaches_the_printable_html(client, db_session):
    _admin(client, db_session)
    order = _order(db_session)
    assert "Продавец" not in client.get(f"/orders/{order.id}/print").text
    _fill(db_session, **FULL)
    html = client.get(f"/orders/{order.id}/print").text
    for value in FULL.values():
        assert value in html, value
    assert html.index("Продавец") < html.index("Заказ покупателя"), (
        "продавец должен идти до заголовка документа — он выдаёт документ"
    )


def test_the_seller_block_reaches_the_pdf_too(client, db_session):
    _admin(client, db_session)
    order = _order(db_session)
    assert "Продавец" not in pdf_text(render_order_pdf(order, db_session))
    _fill(db_session, **FULL)
    text = pdf_text(render_order_pdf(order, db_session))
    for value in FULL.values():
        assert value in text, value
    assert text.index("Продавец") < text.index("Заказ покупателя")


def test_both_paths_read_one_context(db_session):
    order = _order(db_session)
    _fill(db_session, **FULL)
    context = build_order_context(order, db_session)
    assert context["seller"] == [(label, FULL[key]) for key, label in COMPANY_FIELDS]


def test_only_the_filled_company_fields_are_printed(db_session):
    order = _order(db_session)
    _fill(db_session, company_name="ООО Ромашка", company_kpp="770101001")
    context = build_order_context(order, db_session)
    assert context["seller"] == [("Наименование", "ООО Ромашка"), ("КПП", "770101001")]
    html = render_order_html(order, db_session)
    assert "ИНН:" not in html and "Адрес:" not in html


def test_an_empty_company_prints_no_seller_block_at_all(db_session):
    order = _order(db_session)
    _fill(db_session, company_name="   ", company_inn="")
    context = build_order_context(order, db_session)
    assert context["seller"] == []
    assert "Продавец" not in render_order_html(order, db_session)
    assert "Продавец" not in pdf_text(render_order_pdf(order, db_session))


def test_the_seller_survives_an_ampersand_in_both_paths(db_session):
    order = _order(db_session)
    _fill(db_session, company_name="Ромашка & Ко")
    assert "Ромашка &amp; Ко" in render_order_html(order, db_session)
    assert "Ромашка & Ко" in pdf_text(render_order_pdf(order, db_session))