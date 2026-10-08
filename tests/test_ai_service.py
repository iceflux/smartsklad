from datetime import date, datetime, time, timedelta

import pytest

from app.modules.ai.service import (
    FORECAST_HORIZONS,
    LOOKBACK_DAYS,
    MIN_DAYS_FOR_FORECAST,
    RECOMMENDATION_HORIZON,
    _linear_forecast,
    _min_stock,
    forecast_rows,
)
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


def test_constants_match_spec():
    assert LOOKBACK_DAYS == 90
    assert MIN_DAYS_FOR_FORECAST == 14
    assert FORECAST_HORIZONS == (7, 14, 30)
    assert RECOMMENDATION_HORIZON == 30


def test_flat_series_two_per_day():
    assert _linear_forecast([2] * 20, 7) == 14
    assert _linear_forecast([2] * 20, 14) == 28
    assert _linear_forecast([2] * 20, 30) == 60


def test_all_zero_series_gives_zero():
    assert _linear_forecast([0] * 20, 7) == 0


def test_single_point_series_returns_none():
    assert _linear_forecast([5], 7) is None


def test_empty_series_returns_none():
    assert _linear_forecast([], 7) is None


def test_declining_series_predicts_zero():
    assert _linear_forecast(list(range(10, 0, -1)), 30) == 0


def test_declining_series_clamps_each_day():
    assert _linear_forecast([5, 4, 3], 30) == 3


def test_flat_series_one_per_day():
    result = _linear_forecast([1] * 20, 7)
    assert result == 7


def test_min_stock_from_global_days():
    assert _min_stock(None, 2.0, 14) == 28


def test_min_stock_product_override_wins():
    assert _min_stock(5, 2.0, 14) == 5


def test_min_stock_zero_is_explicit_override(db_session):
    from app.modules.ai.service import _min_stock
    assert _min_stock(0, 2.0, 14) == 0


def test_min_stock_zero_demand():
    assert _min_stock(None, 0.0, 14) == 0


def test_min_stock_negative_override_clamped():
    assert _min_stock(-3, 2.0, 14) == 0


def test_min_stock_negative_fallback_clamped():
    assert _min_stock(None, -2.0, 14) == 0


def _customer(db_session):
    c = Customer(name="ООО Строй", personal_discount=0.0)
    db_session.add(c)
    db_session.commit()
    return c


def _product(db_session, sku="SKU-AI", **kwargs):
    kwargs.setdefault("rest", 0)
    p = Product(name="Гвозди", sku=sku, unit_id=_unit_id(db_session), price=10.0, **kwargs)
    db_session.add(p)
    db_session.commit()
    return p


def _sale(db_session, product, customer, *, day, quantity=2, status="done", deleted=False, number="00001"):
    order = Order(
        number=number, customer_id=customer.id, status=status, discount=0.0,
        total_amount=0.0, created_at=datetime.combine(day, time(12, 0)),
    )
    if deleted:
        order.deleted_at = datetime.combine(day, time(13, 0))
    db_session.add(order)
    db_session.commit()
    order.items.append(OrderItem(
        product_id=product.id, product_name_snapshot=product.name,
        product_price_snapshot=product.price, discount_snapshot=0.0,
        quantity=quantity, unit_snapshot=product.unit,
    ))
    db_session.commit()
    return order


def _daily_sales(db_session, product, customer, days, quantity=2, start_number=1, **kwargs):
    today = date.today()
    first = today - timedelta(days=days - 1)
    for i in range(days):
        _sale(db_session, product, customer, day=first + timedelta(days=i),
              quantity=quantity, number=f"{start_number + i:05d}", **kwargs)
    return first


def test_below_min_stock_filter_uses_the_computed_threshold(db_session):
    """Фильтр сравнивает остаток с ПОСЧИТАННЫМ порогом, а не с сырым значением.

    Это главное в фильтре: у товара без своего минимального запаза порог всё
    равно посчитан из «Запас в днях», и человек видит его в колонке «Запас» на
    этой же странице. Сравнение с сырым `min_stock` выбросило бы такой товар
    из отчёта, хотя остаток ниже порога, который перед глазами.
    """
    db = db_session
    customer = _customer(db)
    # Свой минимум задан: остаток ниже — попадает.
    own_low = _product(db, sku="OWN-LOW", rest=5, min_stock=10)
    # Свой минимум задан: остаток выше — не попадает.
    own_ok = _product(db, sku="OWN-OK", rest=50, min_stock=10)
    # Своего минимума нет: порог = продажи × 14 дней, остаток ниже него.
    from_days = _product(db, sku="FROM-DAYS", rest=1)
    for product in (own_low, own_ok, from_days):
        _daily_sales(db, product, customer, 20, quantity=2,
                     start_number=product.sku and 100)

    rows, total = forecast_rows(db, below_min_stock=True)
    skus = {row["sku"] for row in rows}
    assert total == 2, f"под фильтр попали не те товары: {skus}"
    assert own_low.sku in skus
    assert own_ok.sku not in skus
    assert from_days.sku in skus, (
        "товар без своего минимума обязан попасть: его порог посчитан из "
        "«Запас в днях» и виден в колонке «Запас»")
    for row in rows:
        assert row["rest"] < row["min_stock"], "в отчёте попал товар не ниже порога"


def test_below_min_stock_filter_is_off_by_default(db_session):
    """Без параметра фильтр не включается — иначе страница прогноза молча
    потеряла бы товары, потому что по умолчанию показывает все."""
    db = db_session
    customer = _customer(db)
    product = _product(db, sku="ALL", rest=1)
    _daily_sales(db, product, customer, 20)

    rows, total = forecast_rows(db)
    assert total == 1, "товар не попал в обычный прогноз"


def test_below_min_stock_is_always_a_purchase_when_there_are_sales(db_session):
    """Остаток ниже порога ⇒ товар почти всегда и к закупке. Это свойство формулы,
    а не совпадение, и его стоит знать, выбирая фильтры.

    `need = Прогноз(30 дней) + Запас − Остаток`. При `Остаток < Запас` первая
    разность положительна, и прогноз добавляет ещё, то есть `need > 0`
    обязательно. Исключение одно: у товара нет достаточной истории продаж, тогда
    `recommended_qty` остаётся пустым вовсе, а порог — посчитан.

    Проверяется именно исключение: без него фильтр «ниже порога» был бы подмножеством
    «только к закупке», и галочка ничего бы не добавляла.
    """
    db = db_session
    customer = _customer(db)
    with_sales = _product(db, sku="WITH-SALES", rest=1, min_stock=10)
    _daily_sales(db, with_sales, customer, 20, quantity=2)

    below, _ = forecast_rows(db, below_min_stock=True)
    buy, _ = forecast_rows(db, only_purchase=True)
    assert with_sales.sku in {r["sku"] for r in below}
    assert with_sales.sku in {r["sku"] for r in buy}, (
        "товар ниже порога обязан быть и к закупке — так выходит из формулы")

    # Товар без истории продаж: порог посчитан, но рекомендации нет.
    no_sales = _product(db, sku="NO-SALES", rest=1, min_stock=10)
    below, _ = forecast_rows(db, below_min_stock=True)
    buy, _ = forecast_rows(db, only_purchase=True)
    assert no_sales.sku in {r["sku"] for r in below}, "порог посчитан даже без продаж"
    assert no_sales.sku not in {r["sku"] for r in buy}, (
        "без истории продаж рекомендации не бывает, но под «ниже порога» товар попадает")


def test_forecast_rows_counts_sales_days(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, total = forecast_rows(db)
    assert total == 1
    row = rows[0]
    assert row["product_id"] == product.id
    assert row["days_with_sales"] == 20
    assert row["has_data"] is True
    assert row["forecast_7"] is not None and row["forecast_7"] > 0
    assert row["forecast_30"] is not None and row["forecast_30"] > 0


def test_forecast_rows_below_threshold_has_no_data(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db)
    _daily_sales(db, product, customer, 10, start_number=1)
    rows, _ = forecast_rows(db)
    row = rows[0]
    assert row["days_with_sales"] == 10
    assert row["has_data"] is False
    assert row["forecast_7"] is None
    assert row["forecast_30"] is None
    assert row["recommended_qty"] is None


def test_forecast_rows_equals_pure_function_for_window(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, _ = forecast_rows(db, lookback_days=20)
    assert rows[0]["forecast_7"] == _linear_forecast([2] * 20, 7)
    assert rows[0]["forecast_30"] == _linear_forecast([2] * 20, 30)


def test_forecast_rows_recommendation_formula(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db, rest=1)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, _ = forecast_rows(db, lookback_days=20, min_stock_days=14)
    row = rows[0]
    expected_need = row["forecast_30"] + row["min_stock"] - row["rest"]
    assert expected_need > 0
    assert row["recommended_qty"] == expected_need


def test_forecast_rows_no_recommendation_when_stock_enough(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db, rest=100000)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, _ = forecast_rows(db, lookback_days=20)
    assert rows[0]["recommended_qty"] is None


def test_forecast_rows_min_stock_from_global_days(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db, min_stock=None)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, _ = forecast_rows(db, lookback_days=20, min_stock_days=14)
    assert rows[0]["min_stock"] == 28


def test_forecast_rows_min_stock_override(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db, min_stock=5)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, _ = forecast_rows(db, lookback_days=20, min_stock_days=14)
    assert rows[0]["min_stock"] == 5


def test_forecast_rows_ignores_cancelled_orders(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db)
    _daily_sales(db, product, customer, 20, start_number=1, status="cancelled")
    rows, _ = forecast_rows(db, lookback_days=20)
    assert rows[0]["days_with_sales"] == 0
    assert rows[0]["has_data"] is False


def test_forecast_rows_ignores_soft_deleted_orders(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db)
    _daily_sales(db, product, customer, 20, start_number=1, deleted=True)
    rows, _ = forecast_rows(db, lookback_days=20)
    assert rows[0]["days_with_sales"] == 0


def test_forecast_rows_skips_untracked_products(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db, track_rest=False)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, total = forecast_rows(db, lookback_days=20)
    assert total == 0
    assert rows == []


def test_forecast_rows_skips_deleted_products(db_session):
    db = db_session
    customer = _customer(db)
    product = _product(db, is_deleted=True)
    _daily_sales(db, product, customer, 20, start_number=1)
    rows, total = forecast_rows(db, lookback_days=20)
    assert total == 0


def test_forecast_rows_only_purchase_filter(db_session):
    db = db_session
    customer = _customer(db)
    need = _product(db, sku="SKU-NEED", rest=0)
    enough = _product(db, sku="SKU-ENOUGH", rest=100000)
    _daily_sales(db, need, customer, 20, start_number=1)
    _daily_sales(db, enough, customer, 20, start_number=21)
    rows, total = forecast_rows(db, lookback_days=20, only_purchase=True)
    assert total == 1
    assert rows[0]["product_id"] == need.id
    assert rows[0]["recommended_qty"] is not None


def test_forecast_rows_pagination(db_session):
    db = db_session
    customer = _customer(db)
    for i in range(5):
        p = _product(db, sku=f"SKU-P{i}", rest=0)
        _daily_sales(db, p, customer, 20, start_number=1 + i * 20)
    rows, total = forecast_rows(db, lookback_days=20, page=1, page_size=2)
    assert total == 5
    assert len(rows) == 2
    rows2, _ = forecast_rows(db, lookback_days=20, page=2, page_size=2)
    assert len(rows2) == 2
    assert rows[0]["product_id"] != rows2[0]["product_id"]


def test_forecast_rows_product_id_filter(db_session):
    db = db_session
    customer = _customer(db)
    first = _product(db, sku="SKU-F1")
    second = _product(db, sku="SKU-F2")
    _daily_sales(db, first, customer, 20, start_number=1)
    _daily_sales(db, second, customer, 20, start_number=21)
    rows, total = forecast_rows(db, lookback_days=20, product_id=first.id)
    assert total == 1
    assert rows[0]["product_id"] == first.id


def _sales_on_days(db_session, product, customer, day_offsets, quantity=2, start_number=1):
    today = date.today()
    for i, offset in enumerate(day_offsets):
        _sale(db_session, product, customer, day=today - timedelta(days=offset),
              quantity=quantity, number=f"{start_number + i:05d}")


def test_forecast_rows_dense_series_zero_fills_gaps(db_session):
    offsets = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14}
    customer = _customer(db_session)
    product = _product(db_session)
    _sales_on_days(db_session, product, customer, sorted(offsets))
    rows, _ = forecast_rows(db_session, lookback_days=20, min_stock_days=14)
    row = rows[0]
    assert row["days_with_sales"] == 14
    assert row["avg_daily_sold"] == pytest.approx(1.4)
    assert row["min_stock"] == 20
    dense = [2 if (19 - i) in offsets else 0 for i in range(20)]
    assert row["forecast_7"] == _linear_forecast(dense, 7)


def test_forecast_rows_no_data_still_computes_min_stock(db_session):
    customer = _customer(db_session)
    product = _product(db_session)
    _daily_sales(db_session, product, customer, 5, start_number=1)
    rows, _ = forecast_rows(db_session, lookback_days=20, min_stock_days=14)
    row = rows[0]
    assert row["has_data"] is False
    assert row["min_stock"] == 7


def test_forecast_rows_reports_rest_and_default_min_stock_days(db_session):
    customer = _customer(db_session)
    product = _product(db_session, rest=1, min_stock=None)
    _daily_sales(db_session, product, customer, 20, start_number=1)
    rows, _ = forecast_rows(db_session, lookback_days=20, min_stock_days=None)
    row = rows[0]
    assert row["rest"] == 1
    assert row["min_stock"] == 28
