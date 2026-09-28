from datetime import date, datetime, time, timedelta

import pytest

from app.modules.customers.models import Customer
from app.modules.dashboard import service as dashboard_service
from app.modules.dashboard.service import (
    DEFAULT_PRESET,
    PRESET_DAYS,
    chart_bars,
    dashboard_metrics,
    dashboard_sales_csv,
    earliest_order_date,
    resolve_period,
)
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


def test_preset_windows_are_rolling_and_end_today():
    today = date.today()
    assert resolve_period("day") == (today, today)
    assert resolve_period("week") == (today - timedelta(days=6), today)
    assert resolve_period("month") == (today - timedelta(days=29), today)
    assert resolve_period("year") == (today - timedelta(days=364), today)


def test_preset_window_lengths():
    today = date.today()
    for preset, length in (("day", 1), ("week", 7), ("month", 30), ("year", 365)):
        start, end = resolve_period(preset)
        assert (end - start).days + 1 == length


def test_unknown_preset_falls_back_to_day():
    assert resolve_period("nonsense") == (date.today(), date.today())
    assert resolve_period(None) == (date.today() - timedelta(days=29), date.today())


def test_only_date_from_defaults_end_to_today():
    start, end = resolve_period("month", "2026-01-01")
    assert start == date(2026, 1, 1)
    assert end == date.today()


def test_only_date_to_defaults_start_to_29_days_back():
    start, end = resolve_period("month", None, "2026-01-31")
    assert end == date(2026, 1, 31)
    assert start == end - timedelta(days=29)


def test_explicit_dates_win_over_preset():
    assert resolve_period("year", "2026-02-01", "2026-02-05") == (date(2026, 2, 1), date(2026, 2, 5))


def test_garbage_date_is_treated_as_absent():
    start, end = resolve_period("week", "не дата")
    assert end == date.today()
    assert (end - start).days + 1 == 7


def test_inverted_period_raises():
    with pytest.raises(ValueError):
        resolve_period(None, "2026-02-01", "2026-01-01")


def test_future_date_from_raises_even_without_date_to():
    with pytest.raises(ValueError):
        resolve_period("day", date.today() + timedelta(days=1))


def test_date_objects_are_accepted():
    assert resolve_period("month", date(2026, 3, 1), date(2026, 3, 2)) == (date(2026, 3, 1), date(2026, 3, 2))


def _frozen_date(day):
    class _Frozen(date):
        @classmethod
        def today(cls):
            return day
    return _Frozen


@pytest.mark.parametrize("today,preset,expected", [
    (date(2026, 5, 17), "quarter", (date(2026, 4, 1), date(2026, 5, 17))),
    (date(2026, 5, 17), "prev_quarter", (date(2026, 1, 1), date(2026, 3, 31))),
    (date(2026, 1, 3), "prev_quarter", (date(2025, 10, 1), date(2025, 12, 31))),
    (date(2026, 12, 31), "quarter", (date(2026, 10, 1), date(2026, 12, 31))),
    (date(2026, 5, 17), "cal_year", (date(2026, 1, 1), date(2026, 5, 17))),
    (date(2026, 5, 17), "year", (date(2025, 5, 18), date(2026, 5, 17))),
])
def test_calendar_presets(monkeypatch, today, preset, expected):
    monkeypatch.setattr(dashboard_service, "date", _frozen_date(today))
    assert dashboard_service.resolve_period(preset) == expected


def test_quarter_and_previous_quarter_are_adjacent(monkeypatch):
    monkeypatch.setattr(dashboard_service, "date", _frozen_date(date(2026, 5, 17)))
    start, end = dashboard_service.resolve_period("quarter")
    prev_start, prev_end = dashboard_service.resolve_period("prev_quarter")
    assert prev_end + timedelta(days=1) == start


def test_explicit_dates_still_win_over_a_calendar_preset(monkeypatch):
    monkeypatch.setattr(dashboard_service, "date", _frozen_date(date(2026, 5, 17)))
    assert dashboard_service.resolve_period("all", "2026-02-01", "2026-02-05") == (
        date(2026, 2, 1), date(2026, 2, 5),
    )


def test_all_period_starts_at_the_earliest_order(db_session):
    customer = _customer(db_session)
    nails = _product(db_session)
    _order(db_session, customer, nails, number="00001", day=date(2026, 3, 10), amount=100.0)
    _order(db_session, customer, nails, number="00002", day=date(2024, 3, 1), amount=50.0)
    earliest = earliest_order_date(db_session)
    assert earliest == date(2024, 3, 1)
    assert resolve_period("all", earliest=earliest)[0] == date(2024, 3, 1)


def test_all_period_without_orders_is_today_only(db_session):
    assert earliest_order_date(db_session) is None
    today = date.today()
    assert resolve_period("all", earliest=None) == (today, today)


def test_earliest_order_date_uses_the_time_part_of_created_at(db_session):
    customer = _customer(db_session)
    nails = _product(db_session)
    _order(db_session, customer, nails, number="00001", day=date(2026, 3, 10),
           amount=100.0, hour=23, minute=59)
    assert earliest_order_date(db_session) == date(2026, 3, 10)


def _daily(amounts):
    start = date(2026, 3, 1)
    return [{"date": start + timedelta(days=i), "orders": 1, "amount": a} for i, a in enumerate(amounts)]


def test_chart_bars_empty_series():
    chart = chart_bars([])
    assert chart["bars"] == []
    assert chart["width"] == 600
    assert chart["height"] == 180


def test_chart_bars_single_day():
    chart = chart_bars(_daily([100.0]))
    assert len(chart["bars"]) == 1
    assert chart["bars"][0]["amount"] == 100.0


def test_chart_bars_zero_totals_do_not_divide_by_zero():
    chart = chart_bars(_daily([0.0, 0.0, 0.0]))
    assert [b["h"] for b in chart["bars"]] == [0, 0, 0]


def test_chart_bars_proportional_heights():
    chart = chart_bars(_daily([100.0, 200.0, 300.0]))
    assert [b["h"] for b in chart["bars"]] == [47, 93, 140]


def test_chart_bars_stay_inside_viewbox():
    for count in (1, 5, 7, 30, 275, 276, 365):
        chart = chart_bars(_daily([1.0] * count))
        n = len(chart["bars"])
        last = chart["bars"][-1]
        assert last["x"] + last["w"] <= chart["width"]
        assert chart["width"] >= 50 + n
    assert chart_bars(_daily([1.0] * 365))["width"] == 780


def test_chart_bars_long_series_thins_labels():
    chart = chart_bars(_daily([1.0] * 30))
    assert [b["label"] for b in chart["bars"] if b["label"]] == [
        "01.03",
        "06.03",
        "11.03",
        "16.03",
        "21.03",
        "26.03",
    ]


def _customer(db_session, name="ООО Строй"):
    c = Customer(name=name, personal_discount=0.0)
    db_session.add(c)
    db_session.commit()
    return c


def _product(db_session, name="Гвозди", sku="SKU-D1"):
    p = Product(name=name, sku=sku, unit_id=_unit_id(db_session), price=10.0, rest=10)
    db_session.add(p)
    db_session.commit()
    return p


def _order(db_session, customer, product, *, number, day, amount, quantity=1, status="done", deleted=False, hour=12, minute=0, second=0):
    o = Order(
        number=number, customer_id=customer.id, status=status, discount=0.0,
        total_amount=amount, created_at=datetime.combine(day, time(hour, minute, second)),
    )
    if deleted:
        o.deleted_at = datetime.combine(day, time(hour, minute, second))
    db_session.add(o)
    db_session.commit()
    o.items.append(OrderItem(
        product_id=product.id, product_name_snapshot=product.name,
        product_price_snapshot=10.0, discount_snapshot=0.0,
        quantity=quantity, unit_snapshot="шт",
    ))
    db_session.commit()
    return o


def test_dashboard_metrics_totals_and_avg_check(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    day = date(2026, 3, 10)
    _order(db_session, customer, product, number="00001", day=day, amount=100.0)
    _order(db_session, customer, product, number="00002", day=day, amount=200.0)
    _order(db_session, customer, product, number="00003", day=day, amount=50.0)
    m = dashboard_metrics(db_session, day, day)
    assert m["orders_count"] == 3
    assert m["total_sum"] == 350.0
    assert m["avg_check"] == 116.67
    assert m["date_from"] == day
    assert m["date_to"] == day


def test_dashboard_metrics_empty_period(db_session):
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 5))
    assert m["orders_count"] == 0
    assert m["total_sum"] == 0.0
    assert m["avg_check"] == 0.0
    assert len(m["daily"]) == 5
    assert all(row["orders"] == 0 for row in m["daily"])


def test_dashboard_metrics_daily_is_dense(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    _order(db_session, customer, product, number="00001", day=date(2026, 3, 1), amount=100.0)
    _order(db_session, customer, product, number="00002", day=date(2026, 3, 3), amount=300.0)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 5))
    assert [row["orders"] for row in m["daily"]] == [1, 0, 1, 0, 0]
    assert [row["amount"] for row in m["daily"]] == [100.0, 0.0, 300.0, 0.0, 0.0]


def test_dashboard_metrics_inclusive_bounds(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    _order(db_session, customer, product, number="00001", day=date(2026, 3, 1), amount=100.0, hour=0)
    _order(db_session, customer, product, number="00002", day=date(2026, 3, 5), amount=200.0, hour=23)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 5))
    assert m["orders_count"] == 2
    assert m["total_sum"] == 300.0


def test_dashboard_metrics_includes_last_second_of_period(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    _order(db_session, customer, product, number="00001", day=date(2026, 3, 5), amount=100.0,
           hour=23, minute=59, second=59)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 5))
    assert m["orders_count"] == 1
    assert m["total_sum"] == 100.0
    assert m["daily"][-1]["amount"] == 100.0


def test_dashboard_metrics_excludes_not_done(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    _order(db_session, customer, product, number="00001", day=date(2026, 3, 1), amount=100.0, status="new")
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert m["orders_count"] == 0
    assert m["daily"][0]["amount"] == 0.0


def test_dashboard_metrics_excludes_soft_deleted(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    _order(db_session, customer, product, number="00001", day=date(2026, 3, 1), amount=100.0, deleted=True)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert m["orders_count"] == 0


def test_dashboard_metrics_top_products_by_quantity(db_session):
    customer = _customer(db_session)
    nails = _product(db_session, name="Гвозди", sku="SKU-T1")
    screws = _product(db_session, name="Шурупы", sku="SKU-T2")
    _order(db_session, customer, nails, number="00001", day=date(2026, 3, 1), amount=100.0, quantity=2)
    _order(db_session, customer, screws, number="00002", day=date(2026, 3, 1), amount=100.0, quantity=5)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert [row["name"] for row in m["top_products"]] == ["Шурупы", "Гвозди"]
    assert [row["quantity"] for row in m["top_products"]] == [5, 2]


def test_dashboard_metrics_top_products_limit(db_session):
    customer = _customer(db_session)
    for i in range(12):
        product = _product(db_session, name=f"Товар {i}", sku=f"SKU-L{i}")
        _order(db_session, customer, product, number=f"{i + 1:05d}", day=date(2026, 3, 1), amount=10.0, quantity=i + 1)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert len(m["top_products"]) == 10
    assert m["top_products"][0]["quantity"] == 12


def test_dashboard_metrics_top_customers_by_sum(db_session):
    product = _product(db_session)
    small = _customer(db_session, "Малый клиент")
    big = _customer(db_session, "Крупный клиент")
    _order(db_session, small, product, number="00001", day=date(2026, 3, 1), amount=100.0)
    _order(db_session, big, product, number="00002", day=date(2026, 3, 1), amount=900.0)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert [row["name"] for row in m["top_customers"]] == ["Крупный клиент", "Малый клиент"]
    assert m["top_customers"][0]["total_sum"] == 900.0
    assert m["top_customers"][0]["orders_count"] == 1


def test_dashboard_metrics_top_customers_keeps_soft_deleted(db_session):
    product = _product(db_session)
    gone = _customer(db_session, "Удалённый клиент")
    _order(db_session, gone, product, number="00001", day=date(2026, 3, 1), amount=100.0)
    gone.is_deleted = True
    db_session.commit()
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert [row["name"] for row in m["top_customers"]] == ["Удалённый клиент"]


def test_dashboard_metrics_tops_tie_break_by_name(db_session):
    product = _product(db_session)
    beta = _customer(db_session, "Бета")
    alpha = _customer(db_session, "Альфа")
    _order(db_session, beta, product, number="00001", day=date(2026, 3, 1), amount=100.0)
    _order(db_session, alpha, product, number="00002", day=date(2026, 3, 1), amount=100.0)
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert [row["name"] for row in m["top_customers"]] == ["Альфа", "Бета"]


def test_dashboard_metrics_tops_duplicate_names_break_tie_by_id(db_session):
    customer = _customer(db_session)
    first = _product(db_session, name="Гвозди", sku="SKU-DUP1")
    second = _product(db_session, name="Гвозди", sku="SKU-DUP2")
    day = date(2026, 3, 1)
    _order(db_session, customer, first, number="00001", day=day, amount=100.0, quantity=4)
    _order(db_session, customer, second, number="00002", day=day, amount=100.0, quantity=4)
    m = dashboard_metrics(db_session, day, day)
    assert [row["product_id"] for row in m["top_products"]] == [first.id, second.id]
    assert [row["name"] for row in m["top_products"]] == ["Гвозди", "Гвозди"]


def test_dashboard_metrics_empty_preset_uses_default_window(db_session):
    start, end = resolve_period("", None, None)
    expected_start, expected_end = resolve_period(None, None, None)
    assert (start, end) == (expected_start, expected_end)
    assert (end - start).days == PRESET_DAYS[DEFAULT_PRESET] - 1


def test_dashboard_sales_csv_shape():
    daily = [
        {"date": date(2026, 3, 1), "orders": 2, "amount": 150.0},
        {"date": date(2026, 3, 2), "orders": 0, "amount": 0.0},
    ]
    raw = dashboard_sales_csv(daily)
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8").replace("\ufeff", "", 1)
    lines = text.splitlines()
    assert lines[0] == "date;orders;amount"
    assert lines[1] == "2026-03-01;2;150.0"
    assert lines[2] == "2026-03-02;0;0.0"


def test_dashboard_metrics_top_products_sums_repeated_items(db_session):
    customer = _customer(db_session)
    nails = _product(db_session, name="Гвозди", sku="SKU-S1")
    day = date(2026, 3, 1)
    _order(db_session, customer, nails, number="00001", day=day, amount=100.0, quantity=2)
    _order(db_session, customer, nails, number="00002", day=day, amount=100.0, quantity=3)
    m = dashboard_metrics(db_session, day, day)
    assert [row["name"] for row in m["top_products"]] == ["Гвозди"]
    assert m["top_products"][0]["quantity"] == 5


def test_dashboard_metrics_top_customers_sums_repeated_orders(db_session):
    product = _product(db_session)
    buyer = _customer(db_session, "Оптовый клиент")
    day = date(2026, 3, 1)
    _order(db_session, buyer, product, number="00001", day=day, amount=100.0)
    _order(db_session, buyer, product, number="00002", day=day, amount=250.0)
    m = dashboard_metrics(db_session, day, day)
    assert [row["name"] for row in m["top_customers"]] == ["Оптовый клиент"]
    assert m["top_customers"][0]["total_sum"] == 350.0
    assert m["top_customers"][0]["orders_count"] == 2


def test_dashboard_metrics_top_products_uses_live_name_and_keeps_deleted(db_session):
    customer = _customer(db_session)
    product = _product(db_session, name="Старое", sku="SKU-LV")
    _order(db_session, customer, product, number="00001", day=date(2026, 3, 1), amount=100.0, quantity=4)
    product.name = "Новое"
    product.is_deleted = True
    db_session.commit()
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert [row["name"] for row in m["top_products"]] == ["Новое"]


def test_dashboard_metrics_top_products_tie_break_by_name(db_session):
    customer = _customer(db_session)
    beta = _product(db_session, name="Бета", sku="SKU-TB")
    alpha = _product(db_session, name="Альфа", sku="SKU-TA")
    day = date(2026, 3, 1)
    _order(db_session, customer, beta, number="00001", day=day, amount=100.0, quantity=3)
    _order(db_session, customer, alpha, number="00002", day=day, amount=100.0, quantity=3)
    m = dashboard_metrics(db_session, day, day)
    assert [row["name"] for row in m["top_products"]] == ["Альфа", "Бета"]


def test_dashboard_metrics_top_customers_limit(db_session):
    product = _product(db_session)
    for i in range(12):
        buyer = _customer(db_session, f"Клиент {i}")
        _order(db_session, buyer, product, number=f"{i + 1:05d}", day=date(2026, 3, 1), amount=float((i + 1) * 10))
    m = dashboard_metrics(db_session, date(2026, 3, 1), date(2026, 3, 1))
    assert len(m["top_customers"]) == 10
    assert m["top_customers"][0]["name"] == "Клиент 11"


def test_dashboard_metrics_avg_check_uses_rounded_total(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    day = date(2026, 3, 10)
    _order(db_session, customer, product, number="00001", day=day, amount=10.004)
    _order(db_session, customer, product, number="00002", day=day, amount=10.004)
    m = dashboard_metrics(db_session, day, day)
    assert m["total_sum"] == 20.01
    assert m["avg_check"] == 10.01
