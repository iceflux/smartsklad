import re
from pathlib import Path

from reportlab.lib.styles import getSampleStyleSheet

from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name
from app.modules.orders.service import create_order
from app.modules.printing import service as printing_service
from app.modules.printing.service import render_order_html, render_order_pdf
from app.modules.settings.service import set_setting
from tests.test_seller_on_document import pdf_text

SOURCE = Path(printing_service.__file__).read_bytes().decode("utf-8")


def _order(db_session, customer_name="ООО Строй", product_name="Гвозди"):
    product = Product(name=product_name, sku="NAIL-1",
                      unit_id=get_unit_by_name(db_session, "шт").id,
                      price=100.0, rest=50)
    customer = Customer(name=customer_name, email="a@b.ru")
    db_session.add_all([product, customer])
    db_session.commit()
    return create_order(db_session, customer_id=customer.id, discount=0,
                        items=[{"product_id": product.id, "quantity": 2, "discount": 0}])


def _flat(pdf: bytes) -> str:
    """Текст PDF без переносов строк.

    ReportLab переносит длинный абзац по ширине колонки, и каждая строка
    приходит из pdf_text отдельным куском, то есть значение в выдаче разорвано
    переводом строки там, где в базе его нет. Сверка значения посимвольно не
    различает «исказили текст» и «перенесли строку», поэтому переносы снимаются.
    """
    return pdf_text(pdf).replace("\n", "")


def test_a_customer_name_that_looks_like_a_tag_still_builds_the_pdf(db_session):
    order = _order(db_session, customer_name="Ромашка <b> Ко")
    assert "Ромашка <b> Ко" in _flat(render_order_pdf(order, db_session))


def test_a_customer_name_with_markup_is_not_rendered_as_markup(db_session):
    order = _order(db_session, customer_name="Ромашка <b>&amp;</b> Ко")
    assert "Ромашка <b>&amp;</b> Ко" in _flat(render_order_pdf(order, db_session)), (
        "разметка из данных попала в абзац и исказила текст"
    )


def test_the_ampersand_and_the_bracket_reach_both_paths_verbatim(db_session):
    order = _order(db_session, customer_name="Ромашка & Ко")
    assert "Ромашка &amp; Ко" in render_order_html(order, db_session)
    assert "Ромашка & Ко" in _flat(render_order_pdf(order, db_session))


def test_every_company_field_survives_a_tag_like_value(db_session):
    order = _order(db_session)
    set_setting(db_session, "company_name", "ООО Ромашка <b>", user=None)
    set_setting(db_session, "company_address", "Москва, ул. Лесная, 1 & 3", user=None)
    text = _flat(render_order_pdf(order, db_session))
    assert "ООО Ромашка <b>" in text
    assert "Москва, ул. Лесная, 1 & 3" in text


def test_a_product_name_with_a_bracket_stays_in_the_table(db_session):
    order = _order(db_session, product_name="Болт <b> М8")
    assert "Болт <b> М8" in _flat(render_order_pdf(order, db_session))


def test_the_table_cells_stay_plain_strings():
    """Ячейка Table не разбирается как разметка, и это держится проверкой.

    ReportLab читает текст абзаца как подмножество HTML, а строку в ячейке
    Table рисует буквально. Если кто-то завернёт ячейку в Paragraph ради
    переноса строки, болт «<b>» начнёт ломать сборку — и уже молча, потому
    что ни один тест до этого не смотрел в ячейки.
    """
    start = SOURCE.index("header = [")
    end = SOURCE.index("story.append(table)")
    assert "Paragraph(" not in SOURCE[start:end]


def test_the_helper_returns_a_paragraph_with_the_value_verbatim():
    paragraph = printing_service._para(
        "Клиент: {}", getSampleStyleSheet()["Normal"], "Ромашка & Ко <b>")
    assert paragraph.getPlainText() == "Клиент: Ромашка & Ко <b>"


def test_the_helper_leaves_a_literal_without_substitutions_alone():
    paragraph = printing_service._para("Продавец", getSampleStyleSheet()["Normal"])
    assert paragraph.getPlainText() == "Продавец"


def test_the_only_paragraph_of_the_module_is_the_escaping_helper():
    calls = [line for line in SOURCE.splitlines() if re.search(r"\bParagraph\(", line)]
    helper = [line for line in calls if "return Paragraph(" in line]
    assert len(calls) == len(helper) == 1, (
        "новый абзац строится в обход _para — значение попадёт в разбор "
        "ReportLab без экранирования"
    )


def test_no_paragraph_is_built_from_an_interpolated_string():
    for line in SOURCE.splitlines():
        if "_para(" not in line or line.lstrip().startswith("def "):
            continue
        assert not re.search(r"_para\(\s*f[\"']|_para\([^\"']*[\"']\s*\+", line), line