import csv
import io
import math
from datetime import date, datetime, time, timedelta

from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.orders.models import Order, OrderItem

PRESET_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
DEFAULT_PRESET = "month"
CHART_LEFT = 50
CHART_TOP = 20
CHART_BOTTOM = 20
CHART_LABEL_OFFSET = 6
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
        resolved_start, resolved_end = start, end
    elif start is not None:
        resolved_start, resolved_end = start, today
    elif end is not None:
        resolved_start = end - timedelta(days=PRESET_DAYS[DEFAULT_PRESET] - 1)
        resolved_end = end
    else:
        key = preset or DEFAULT_PRESET
        days = PRESET_DAYS.get(key, PRESET_DAYS["day"])
        resolved_start = today - timedelta(days=days - 1)
        resolved_end = today
    if resolved_start > resolved_end:
        raise ValueError("период: начало позже конца")
    return resolved_start, resolved_end


def chart_bars(daily: list[dict], *, width: int = 600, height: int = 180) -> dict:
    layout = {
        "left": CHART_LEFT,
        "axis_y": height - CHART_BOTTOM,
        "label_y": height - CHART_LABEL_OFFSET,
    }
    n = len(daily)
    if n == 0:
        return {"width": width, "height": height, "bars": [], **layout}
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
    return {"width": total_w, "height": height, "bars": bars, **layout}


def _sales_orders(db, date_from: date, date_to: date) -> list[Order]:
    return (
        db.query(Order)
        .filter(
            Order.status == "done",
            Order.deleted_at.is_(None),
            Order.created_at >= datetime.combine(date_from, time.min),
            Order.created_at <= datetime.combine(date_to, time.max),
        )
        .order_by(Order.id)
        .all()
    )


def dashboard_metrics(db, date_from: date, date_to: date, *, top_limit: int = 10) -> dict:
    orders = _sales_orders(db, date_from, date_to)
    total_sum = round(sum(o.total_amount for o in orders), 2)
    orders_count = len(orders)
    per_day = {}
    for order in orders:
        entry = per_day.setdefault(order.created_at.date(), {"orders": 0, "amount": 0.0})
        entry["orders"] += 1
        entry["amount"] += order.total_amount
    daily = []
    for offset in range((date_to - date_from).days + 1):
        day = date_from + timedelta(days=offset)
        entry = per_day.get(day, {"orders": 0, "amount": 0.0})
        daily.append({
            "date": day,
            "orders": entry["orders"],
            "amount": round(entry["amount"], 2),
        })
    customer_ids = {o.customer_id for o in orders}
    quantities = {}
    order_ids = [o.id for o in orders]
    if order_ids:
        rows = (
            db.query(OrderItem.product_id, OrderItem.quantity)
            .filter(OrderItem.order_id.in_(order_ids))
            .all()
        )
        for product_id, quantity in rows:
            quantities[product_id] = quantities.get(product_id, 0) + quantity
    product_names = {
        p.id: p.name
        for p in db.query(Product).filter(Product.id.in_(quantities)).all()
    }
    top_products = sorted(
        (
            {"product_id": pid, "name": product_names.get(pid, ""), "quantity": qty}
            for pid, qty in quantities.items()
        ),
        key=lambda r: (-r["quantity"], r["name"], r["product_id"]),
    )[:top_limit]
    per_customer = {}
    for order in orders:
        entry = per_customer.setdefault(
            order.customer_id, {"orders_count": 0, "total_sum": 0.0}
        )
        entry["orders_count"] += 1
        entry["total_sum"] += order.total_amount
    customer_names = {
        c.id: c.name
        for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()
    }
    top_customers = sorted(
        (
            {
                "customer_id": cid,
                "name": customer_names.get(cid, ""),
                "orders_count": data["orders_count"],
                "total_sum": round(data["total_sum"], 2),
            }
            for cid, data in per_customer.items()
        ),
        key=lambda r: (-r["total_sum"], r["name"], r["customer_id"]),
    )[:top_limit]
    return {
        "date_from": date_from,
        "date_to": date_to,
        "orders_count": orders_count,
        "total_sum": total_sum,
        "avg_check": round(total_sum / orders_count, 2) if orders_count else 0.0,
        "daily": daily,
        "top_products": top_products,
        "top_customers": top_customers,
    }


def dashboard_sales_csv(daily: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["date", "orders", "amount"])
    for row in daily:
        writer.writerow([row["date"].isoformat(), row["orders"], row["amount"]])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")

