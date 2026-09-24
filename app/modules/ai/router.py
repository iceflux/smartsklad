from fastapi import APIRouter, Depends, Query

from app.db import get_db
from app.modules.ai.service import forecast_rows
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.permissions import Permission
from app.modules.settings.service import DEFAULT_MIN_STOCK_DAYS, MIN_STOCK_DAYS_KEY, get_int_setting

router = APIRouter()


@router.get("/forecast")
def api_forecast(
    only_purchase: bool = False,
    product_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db=Depends(get_db),
    user=Depends(require_permissions(Permission.analytics_view)),
):
    rows, _total = forecast_rows(
        db,
        only_purchase=only_purchase,
        product_id=product_id,
        page=page,
        page_size=page_size,
        min_stock_days=get_int_setting(db, MIN_STOCK_DAYS_KEY, DEFAULT_MIN_STOCK_DAYS),
    )
    return rows
