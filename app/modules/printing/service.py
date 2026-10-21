from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from jinja2 import Environment, FileSystemLoader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.formatting import group_number, human_date
from app.modules.customers.models import Customer
from app.modules.settings.service import (
    COMPANY_FIELDS,
    DEFAULT_SEARCH_SLOW_MS,
    company_data,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
JINJA = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
JINJA.globals["maintenance_state"] = lambda: {"mode": "idle", "starts_at": None, "starts_at_text": None, "seconds_left": 0}
JINJA.globals["nav_is_current"] = lambda path, href: False
JINJA.globals["SEARCH_SLOW_DEFAULT"] = DEFAULT_SEARCH_SLOW_MS
JINJA.filters["num"] = group_number
JINJA.filters["dt"] = human_date


def _register_cyrillic_font() -> dict | None:
    windows = Path("C:/Windows/Fonts")
    fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    candidates = (
        (windows / "arial.ttf", windows / "arialbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
         Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")),
        (fonts_dir / "DejaVuSans.ttf", fonts_dir / "DejaVuSans-Bold.ttf"),
        (fonts_dir / "Vera.ttf", fonts_dir / "VeraBd.ttf"),
    )
    for font_path, bold_path in candidates:
        if not font_path.exists():
            continue
        family = font_path.stem
        pdfmetrics.registerFont(TTFont(family, str(font_path)))
        family_bold = family
        if bold_path.exists():
            family_bold = family + "-Bold"
            pdfmetrics.registerFont(TTFont(family_bold, str(bold_path)))
        return {"family": family, "family_bold": family_bold}
    return None


def _para(text: str, style, *values) -> Paragraph:
    """Единственный способ собрать абзац для PDF.

    ReportLab разбирает текст абзаца как подмножество HTML: `<` с латинской
    буквой открывает «тег», который потом не закрывается, и сборка падает
    `ValueError`. Значит любое значение, пришедшее из базы, обязано быть
    экранировано, а экранировать его вручную в каждой строке — значит забыть
    один раз. Здесь значение подставляется в форматную строку и экранируется
    само: подпись пишется в формате, значение — аргументом. Литерал без
    подстановок `format` не трогает.
    """
    return Paragraph(text.format(*(escape(str(value)) for value in values)), style)


def _item_sum(item) -> float:
    return round(item.quantity * item.product_price_snapshot * (1 - item.discount_snapshot / 100.0), 2)


def seller_rows(db) -> list[tuple[str, str]]:
    """Заполненные поля компании по порядку объявления.

    Пустое поле не печатается, а если не заполнено ничего, список пуст и блок
    на документе не появляется вовсе: полупустой блок выглядит поломкой, а
    документ без блока — ровно тот, что был до появления настройки.
    """
    data = company_data(db)
    return [(label, data[key]) for key, label in COMPANY_FIELDS if data[key]]


def build_order_context(order, db) -> dict:
    customer = db.get(Customer, order.customer_id)
    return {
        "number": order.number,
        "customer_name": customer.name if customer and not customer.is_deleted else "",
        "created_at": order.created_at,
        "discount": order.discount,
        "total_amount": order.total_amount,
        "seller": seller_rows(db),
        "items": [
            {
                "name": it.product_name_snapshot,
                "quantity": it.quantity,
                "unit": it.unit_snapshot,
                "price": it.product_price_snapshot,
                "discount": it.discount_snapshot,
                "sum": _item_sum(it),
            }
            for it in order.items
        ],
    }


def render_order_html(order, db) -> str:
    doc = build_order_context(order, db)
    return JINJA.get_template("documents/order_print.html").render(**doc, doc=doc)


def render_order_pdf(order, db) -> bytes:
    ctx = build_order_context(order, db)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    font = _register_cyrillic_font()
    if font:
        styles["Title"].fontName = font["family_bold"]
        styles["Heading2"].fontName = font["family_bold"]
        styles["Normal"].fontName = font["family"]
    story = []
    if ctx["seller"]:
        story.append(_para("Продавец", styles["Heading2"]))
        for label, value in ctx["seller"]:
            story.append(_para("{}: {}", styles["Normal"], label, value))
        story.append(Spacer(1, 6 * mm))
    story.append(_para("Заказ покупателя", styles["Title"]))
    story.append(_para("№ {}", styles["Heading2"], ctx["number"]))
    story.append(_para("Клиент: {}", styles["Normal"], ctx["customer_name"]))
    story.append(_para("Дата: {}", styles["Normal"], human_date(ctx["created_at"])))
    story.append(Spacer(1, 8 * mm))

    header = ["Наименование", "Кол-во", "Ед.", "Цена", "Скидка%", "Сумма"]
    rows = [header]
    for it in ctx["items"]:
        rows.append([
            it["name"], str(it["quantity"]), it["unit"],
            f'{it["price"]:.2f}', f'{it["discount"]:.0f}', f'{it["sum"]:.2f}',
        ])
    rows.append(["", "", "", "", "Итого:", f'{ctx["total_amount"]:.2f}'])
    table = Table(rows, colWidths=[70 * mm, 14 * mm, 12 * mm, 20 * mm, 16 * mm, 20 * mm])
    header_font = font["family_bold"] if font else "Helvetica-Bold"
    body_font = font["family"] if font else "Helvetica"
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -2), 0.3, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("SPAN", (0, -1), (4, -1)),
        ("FONTNAME", (0, 0), (0, 0), header_font),
        ("FONTNAME", (1, 0), (-1, 0), header_font),
        ("FONTNAME", (0, 1), (-1, -1), body_font),
    ]))
    story.append(table)
    story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()