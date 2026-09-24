from app.modules.ai.service import (
    FORECAST_HORIZONS,
    LOOKBACK_DAYS,
    MIN_DAYS_FOR_FOREECAST,
    RECOMMENDATION_HORIZON,
    _linear_forecast,
    _min_stock,
)


def test_constants_match_spec():
    assert LOOKBACK_DAYS == 90
    assert MIN_DAYS_FOR_FOREECAST == 14
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


def test_min_stock_zero_demand():
    assert _min_stock(None, 0.0, 14) == 0


def test_min_stock_negative_override_clamped():
    assert _min_stock(-3, 2.0, 14) == 0


def test_min_stock_negative_fallback_clamped():
    assert _min_stock(None, -2.0, 14) == 0
