from datetime import datetime
from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.modules.customers.models import Customer

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
JINJA = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


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
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -2), 0.3, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("SPAN", (0, -1), (4, -1)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(table)
    story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()