from io import BytesIO
from pathlib import Path

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

from app.formatting import group_number
from app.modules.customers.models import Customer

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
JINJA = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
JINJA.globals["maintenance_state"] = lambda: {"mode": "idle", "starts_at": None, "starts_at_text": None, "seconds_left": 0}
JINJA.globals["nav_is_current"] = lambda path, href: False
JINJA.filters["num"] = group_number


def _register_cyrillic_font() -> dict | None:
    candidates: list[Path] = []
    for name in ("arial.ttf", "Arial.ttf"):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            candidates.append(p)
            break
    if not candidates:
        fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
        for name in ("DejaVuSans.ttf", "Vera.ttf"):
            p = fonts_dir / name
            if p.exists():
                candidates.append(p)
                break
    if not candidates:
        return None
    font_path = candidates[0]
    family = font_path.stem
    pdfmetrics.registerFont(TTFont(family, str(font_path)))
    bold_variant = font_path.with_name(font_path.stem + "bd" + font_path.suffix)
    family_bold = family
    if bold_variant.exists():
        family_bold = family + "-Bold"
        pdfmetrics.registerFont(TTFont(family_bold, str(bold_variant)))
    return {"family": family, "family_bold": family_bold}


def _item_sum(item) -> float:
    return round(item.quantity * item.product_price_snapshot * (1 - item.discount_snapshot / 100.0), 2)


def build_order_context(order, db) -> dict:
    customer = db.get(Customer, order.customer_id)
    return {
        "number": order.number,
        "customer_name": customer.name if customer and not customer.is_deleted else "",
        "created_at": order.created_at.strftime("%d.%m.%Y") if order.created_at else "",
        "discount": order.discount,
        "total_amount": order.total_amount,
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
    return JINJA.get_template("documents/order_print.html").render(**build_order_context(order, db))


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
    story.append(Paragraph("Заказ покупателя", styles["Title"]))
    story.append(Paragraph(f'№ {ctx["number"]}', styles["Heading2"]))
    story.append(Paragraph(f'Клиент: {ctx["customer_name"]}', styles["Normal"]))
    story.append(Paragraph(f'Дата: {ctx["created_at"]}', styles["Normal"]))
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