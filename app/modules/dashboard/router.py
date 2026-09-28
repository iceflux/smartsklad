from fastapi import APIRouter, Depends, HTTPException

from app.db import get_db
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.permissions import Permission
from app.modules.dashboard.service import dashboard_metrics, earliest_order_date, resolve_period

router = APIRouter()


@router.get("/dashboard/sales")
def api_dashboard_sales(
    preset: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db=Depends(get_db),
    user=Depends(require_permissions(Permission.analytics_view)),
):
    try:
        start, end = resolve_period(preset, date_from, date_to, earliest=earliest_order_date(db))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return dashboard_metrics(db, start, end)
