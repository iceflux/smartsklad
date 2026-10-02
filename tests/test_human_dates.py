import re
from datetime import date, datetime
from pathlib import Path

from app.formatting import human_date
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.backup.models import BackupLog
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product, Unit
from app.modules.inventory.service import get_unit_by_name
from app.modules.notifications.models import NotificationLog
from app.modules.notifications.service import message_text
from app.modules.orders.service import create_order
from app.modules.printing import service as print_service

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
PASSWORD = "password123"
STAMP = datetime(2026, 4, 9, 19, 9, 50)


def _stamp_everything(db_session):
    """Проставляет одну и ту же известную дату во все строки, чьи даты
    показывают страницы: у аудита, уведомлений и бэкапов своя колонка времени,
    и без этого проверка знала бы только сегодняшнее число."""
    for model in (AuditLog, NotificationLog, BackupLog):
        for row in db_session.query(model).all():
            row.created_at = STAMP
    for unit in db_session.query(Unit).all():
        unit.updated_at = STAMP
    db_session.commit()


def _admin(client, db_session):
    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _order(db_session):
    product = Product(name="Гвозди", sku="NAIL-1", unit_id=get_unit_by_name(db_session, "шт").id,
                      price=100.0, rest=100000)
    customer = Customer(name="ООО Строй")
    db_session.add_all([product, customer])
    db_session.commit()
    order = create_order(db_session, customer_id=customer.id, discount=0,
                         items=[{"product_id": product.id, "quantity": 1, "discount": 0}])
    order.created_at = STAMP
    order.status = "done"
    db_session.commit()
    return order


def test_a_date_without_a_time_shows_the_day_month_and_year():
    assert human_date(date(2026, 4, 9)) == "09.04.2026"


def test_a_datetime_shows_the_day_first_and_the_time_with_seconds():
    assert human_date(STAMP) == "09.04.2026 19:09:50"


def test_a_datetime_is_checked_before_a_date_because_it_subclasses_it():
    assert isinstance(STAMP, date)
    assert human_date(STAMP) != human_date(date(2026, 4, 9))


def test_a_zero_second_time_is_still_printed_with_its_seconds():
    assert human_date(datetime(2031, 3, 4, 5, 6, 0)) == "04.03.2031 05:06:00"


def test_a_midnight_time_is_not_dropped():
    assert human_date(datetime(2031, 3, 4, 0, 0, 0)) == "04.03.2031 00:00:00"


def test_none_becomes_the_same_dash_as_the_number_filter():
    assert human_date(None) == "—"


def test_a_value_that_is_not_a_date_comes_back_unchanged():
    assert human_date("09.04.2026") == "09.04.2026"
    assert human_date("") == ""


def test_the_filter_is_registered_in_both_jinja_environments():
    from app.main import app
    assert app.state.templates.env.filters["dt"] is human_date
    assert print_service.JINJA.filters["dt"] is human_date


MACHINE_ATTR = re.compile(r'(value|action|href)="[^"]*"')


def _readable(html):
    """Разметка без содержимого атрибутов machine-контекста.

    Проверка «в странице нет ISO» иначе спотыкается о `?date_from=2026-04-09`
    в `action` формы и о то же самое в `value` полей даты — а это ровно то
    место, где ISO и обязан быть. Сами атрибуты проверяет отдельный тест.
    """
    return MACHINE_ATTR.sub("", html)


def test_every_date_column_shows_the_day_first(client, db_session):
    _admin(client, db_session)
    order = _order(db_session)
    db_session.add_all([
        NotificationLog(channel="email", recipient="a@b.ru", status="sent"),
        BackupLog(kind="manual", status="ok"),
    ])
    db_session.commit()
    _stamp_everything(db_session)
    with_time = ["09.04.2026 19:09:50"]
    for path, expected in [
        ("/orders", with_time),
        (f"/orders/{order.id}", with_time),
        ("/customers/1", with_time),
        ("/audit", with_time),
        ("/notifications", with_time),
        ("/backups", with_time),
        ("/units", with_time),
        ("/dashboard?date_from=2026-04-09&date_to=2026-04-30", ["09.04.2026", "30.04.2026"]),
    ]:
        raw = client.get(path).text
        text = re.sub(r"\s+", " ", _readable(raw))
        for want in expected:
            assert want in text, f"{path}: {want!r} не найден"
        assert "2026-04-09" not in text, f"{path}: дата осталась в ISO"


def test_the_notification_message_carries_the_human_date(db_session):
    order = _order(db_session)
    db_session.expire_all()
    order = db_session.get(type(order), order.id)
    assert "от 09.04.2026 19:09:50" in message_text(order, db_session)
    assert "2026-04-09" not in message_text(order, db_session)


def test_no_template_prints_a_date_with_the_python_default_or_an_iso_strftime():
    iso = re.compile(r"strftime\(\s*['\"]%Y-%m-%d")
    bare = re.compile(r"\{\{\s*\w+\.(created_at|updated_at|deleted_at|changed_at)\s*\}\}")
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        assert not iso.search(text), f"{path.name}: strftime с ISO-датой"
        assert not bare.search(text), f"{path.name}: дата печатается без фильтра"


def test_no_python_hands_a_preformatted_date_to_a_template_or_a_message():
    pattern = re.compile(r"(created_at|changed_at|updated_at|deleted_at)\.strftime")
    for path in sorted((Path(__file__).resolve().parents[1] / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = pattern.findall(text)
        assert not found, f"{path.name}: дата форматируется в Python, а не фильтром: {found}"


def test_the_iso_value_of_a_date_input_is_never_reformatted(client, db_session):
    _admin(client, db_session)
    body = client.get("/orders?date_from=2026-04-09&date_to=2026-04-30").text
    inputs = re.findall(r"<input[^>]*type=\"date\"[^>]*>", body)
    assert len(inputs) == 2, inputs
    for tag in inputs:
        assert 'value="2026-04-09"' in tag or 'value="2026-04-30"' in tag, tag
        assert "09.04.2026" not in tag, tag
        assert "30.04.2026" not in tag, tag


def test_the_published_document_shows_the_human_date(db_session):
    order = _order(db_session)
    ctx = print_service.build_order_context(order, db_session)
    html = print_service.render_order_html(order, db_session)
    assert ctx["created_at"] == STAMP
    assert "Дата: 09.04.2026 19:09:50" in html
    assert "2026-04-09" not in html