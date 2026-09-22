from datetime import date

import pytest

from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.service import create_order
from app.modules.printing.service import (
    build_order_context, render_order_html, render_order_pdf,
)


@pytest.fixture
def env(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit="шт", price=100.0, rest=50)
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