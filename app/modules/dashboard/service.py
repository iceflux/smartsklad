import math
from datetime import date, timedelta

PRESET_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
DEFAULT_PRESET = "month"
CHART_LEFT = 50
CHART_TOP = 20
CHART_BOTTOM = 20
LABEL_EVERY = 7


def _parse_iso(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def resolve_period(preset=None, date_from=None, date_to=None) -> tuple[date, date]:
    today = date.today()
    start = _parse_iso(date_from)
    end = _parse_iso(date_to)
    if start is not None and end is not None:
        if start > end:
            raise ValueError("период: начало позже конца")
        return start, end
    if preset is None:
        days = PRESET_DAYS[DEFAULT_PRESET]
    else:
        days = PRESET_DAYS.get(preset, PRESET_DAYS["day"])
    if start is not None:
        return start, today
    if end is not None:
        return end - timedelta(days=PRESET_DAYS["month"] - 1), end
    return today - timedelta(days=days - 1), today


def chart_bars(daily: list[dict], *, width: int = 600, height: int = 180) -> dict:
    n = len(daily)
    if n == 0:
        return {"width": width, "height": height, "bars": []}
    plot_w = width - CHART_LEFT
    bar_w = max(1, plot_w // n - 1)
    step = bar_w + 1
    total_w = CHART_LEFT + n * step
    plot_h = height - CHART_TOP - CHART_BOTTOM
    amounts = [d.get("amount") or 0.0 for d in daily]
    peak = max(amounts) or 1.0
    every = max(1, math.ceil(n / LABEL_EVERY))
    bars = []
    for i, item in enumerate(daily):
        amount = amounts[i]
        bar_h = round(amount / peak * plot_h)
        bars.append({
            "x": CHART_LEFT + i * step,
            "y": CHART_TOP + plot_h - bar_h,
            "w": bar_w,
            "h": bar_h,
            "amount": amount,
            "date": item.get("date"),
            "label": item.get("date").strftime("%d.%m") if i % every == 0 else "",
        })
    return {"width": total_w, "height": height, "bars": bars}
