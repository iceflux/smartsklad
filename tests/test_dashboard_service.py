from datetime import date, timedelta

import pytest

from app.modules.dashboard.service import chart_bars, resolve_period


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
