import math
from datetime import date, datetime, time, timedelta

from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem
from app.modules.settings.service import DEFAULT_MIN_STOCK_DAYS

LOOKBACK_DAYS = 90
MIN_DAYS_FOR_FORECAST = 14
FORECAST_HORIZONS = (7, 14, 30)
RECOMMENDATION_HORIZON = 30


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
    page=1, page_size=25,
) -> tuple[list[dict], int]:
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
    rows.sort(key=lambda r: (-(r["recommended_qty"] or 0), r["name"]))
    total = len(rows)
    offset = (max(1, page) - 1) * page_size
    return rows[offset:offset + page_size], total
