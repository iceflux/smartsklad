import math

from app.modules.settings.service import DEFAULT_MIN_STOCK_DAYS

LOOKBACK_DAYS = 90
MIN_DAYS_FOR_FOREECAST = 14
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
