import math
from datetime import date, datetime, time, timedelta

from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.modules.settings.service import DEFAULT_MIN_STOCK_DAYS
from app.sorting import apply_row_sort

LOOKBACK_DAYS = 90
MIN_DAYS_FOR_FORECAST = 14
FORECAST_HORIZONS = (7, 14, 30)
RECOMMENDATION_HORIZON = 30


def _number(value):
    """Прочерк в порядке — это ноль. Иначе строки «мало данных» оказывались бы то
    выше, то ниже в зависимости от направления, а человек, сортирующий прогноз
    по убыванию, получал бы в конце списка именно те строки, где данных нет."""
    return 0 if value is None else value


FORECAST_SORTS = {
    "name": lambda row: row["name"],
    "sku": lambda row: row["sku"],
    "rest": lambda row: row["rest"],
    "min_stock": lambda row: row["min_stock"],
    "daily": lambda row: row["avg_daily_sold"],
    "f7": lambda row: _number(row["forecast_7"]),
    "f14": lambda row: _number(row["forecast_14"]),
    "f30": lambda row: _number(row["forecast_30"]),
    "buy": lambda row: _number(row["recommended_qty"]),
}
"""Белый список колонок таблицы прогноза. Все девять колонок здесь — значения,
исключать нечего: колонок-действий на этой таблице нет в отличие от страниц
разделов."""


def _second_key(row):
    """Второй ключ сортировки — сам товар, и он обязателен: строки, равные по
    выбранной колонке, без него не имеют определённого порядка, и постраничный
    разбор начинает дублировать и терять их. Именно поэтому он назван, а не
    написано прямо в вызове: по странице его не отличить — строки и так приходят
    в порядке товаров, а сортировка Python устойчива."""
    return row["product_id"]


def _default_order(rows):
    """Порядок, который был до появления сортировки: рекомендация по убыванию,
    затем имя. Страница при загрузке обязана выглядеть как вчера."""
    return sorted(rows, key=lambda row: (-_number(row["recommended_qty"]), row["name"]))


def _linear_forecast(series: list[int], horizon: int) -> int | None:
    n = len(series)
    if n < 2:
        return None
    mean_x = (n - 1) / 2
    mean_y = sum(series) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return None
    slope = sum((i - mean_x) * (series[i] - mean_y) for i in range(n)) / denom
    intercept = mean_y - slope * mean_x
    total = 0.0
    for k in range(horizon):
        day = intercept + slope * (n + k)
        if day > 0:
            total += day
    return max(0, math.ceil(total))


def _min_stock(product_min_stock: int | None, avg_daily_sold: float, min_stock_days: int) -> int:
    if product_min_stock is not None:
        return max(0, product_min_stock)
    return max(0, int(round(avg_daily_sold * min_stock_days)))


def _sales_by_product(db, start: date, end: date) -> dict[int, dict[date, int]]:
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)
    rows = (
        db.query(OrderItem.product_id, OrderItem.quantity, Order.created_at)
        .join(Order, OrderItem.order_id == Order.id)
        .filter(
            Order.status == "done",
            Order.deleted_at.is_(None),
            Order.created_at >= start_dt,
            Order.created_at <= end_dt,
        )
        .all()
    )
    result: dict[int, dict[date, int]] = {}
    for product_id, quantity, created_at in rows:
        per_product = result.setdefault(product_id, {})
        day = created_at.date()
        per_product[day] = per_product.get(day, 0) + quantity
    return result


def forecast_rows(
    db, *, lookback_days=LOOKBACK_DAYS, min_days=MIN_DAYS_FOR_FORECAST,
    only_purchase=False, product_id=None, min_stock_days=None,
    page=1, page_size=25, sort=None, direction=None,
) -> tuple[list[dict], int]:
    page = max(1, page or 1)
    page_size = min(100, max(1, page_size or 25))
    days_setting = DEFAULT_MIN_STOCK_DAYS if min_stock_days is None else min_stock_days
    today = date.today()
    start = today - timedelta(days=lookback_days - 1)
    query = db.query(Product).filter(
        Product.is_deleted.is_(False), Product.track_rest.is_(True)
    )
    if product_id is not None:
        query = query.filter(Product.id == product_id)
    products = query.order_by(Product.id).all()
    sales = _sales_by_product(db, start, today)
    rows = []
    for product in products:
        per_product = sales.get(product.id, {})
        series = [per_product.get(start + timedelta(days=i), 0) for i in range(lookback_days)]
        total_sold = sum(series)
        avg_daily_sold = total_sold / lookback_days
        days_with_sales = sum(1 for q in series if q > 0)
        has_data = days_with_sales >= min_days
        forecasts = {h: None for h in FORECAST_HORIZONS}
        recommended = None
        if has_data:
            for h in FORECAST_HORIZONS:
                forecasts[h] = _linear_forecast(series, h)
            threshold = _min_stock(product.min_stock, avg_daily_sold, days_setting)
            horizon_forecast = forecasts[RECOMMENDATION_HORIZON]
            if horizon_forecast is not None:
                need = horizon_forecast + threshold - product.rest
                recommended = need if need > 0 else None
        else:
            threshold = _min_stock(product.min_stock, avg_daily_sold, days_setting)
        rows.append({
            "product_id": product.id,
            "sku": product.sku,
            "name": product.name,
            "unit": product.unit,
            "rest": product.rest,
            "min_stock": threshold,
            "avg_daily_sold": avg_daily_sold,
            "days_with_sales": days_with_sales,
            "has_data": has_data,
            "forecast_7": forecasts[7],
            "forecast_14": forecasts[14],
            "forecast_30": forecasts[30],
            "recommended_qty": recommended,
        })
    if only_purchase:
        rows = [r for r in rows if r["recommended_qty"] is not None]
    rows = apply_row_sort(rows, FORECAST_SORTS, _default_order, sort, direction,
                          _second_key)
    total = len(rows)
    offset = (max(1, page) - 1) * page_size
    return rows[offset:offset + page_size], total
