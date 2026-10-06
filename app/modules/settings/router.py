from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import get_current_user, require_permissions
from app.modules.auth.permissions import Permission
from app.modules.settings.models import Setting
from app.modules.settings.service import (
    MASKED_KEYS,
    SEARCH_SLOW_MS_KEY,
    SEARCH_SLOW_MS_MAX,
    SEARCH_SLOW_MS_MIN,
    STOCK_TRACK_DEFAULT_KEY,
    get_setting,
    is_user_setting,
    mask_secret,
    set_setting,
)

router = APIRouter()


class StockTrackIn(BaseModel):
    value: str


class SearchSlowIn(BaseModel):
    value: int


def _all_settings(db):
    rows = db.query(Setting).order_by(Setting.key).all()
    return [
        {"key": r.key, "value": mask_secret(r.value) if r.key in MASKED_KEYS else r.value}
        for r in rows
        if not is_user_setting(r.key)
    ]


@router.get("/settings")
def api_get_settings(user=Depends(get_current_user), db=Depends(get_db)):
    return _all_settings(db)


@router.put("/settings/stock-track")
def api_set_stock_track(payload: StockTrackIn, user=Depends(require_permissions(Permission.settings_change)), db=Depends(get_db)):
    if payload.value not in ("true", "false"):
        raise HTTPException(status_code=422, detail="value must be 'true' or 'false'")
    set_setting(db, STOCK_TRACK_DEFAULT_KEY, payload.value, user=user)
    return {"key": STOCK_TRACK_DEFAULT_KEY, "value": get_setting(db, STOCK_TRACK_DEFAULT_KEY)}


@router.put("/settings/search-slow")
def api_set_search_slow(payload: SearchSlowIn, user=Depends(require_permissions(Permission.settings_change)), db=Depends(get_db)):
    if not SEARCH_SLOW_MS_MIN <= payload.value <= SEARCH_SLOW_MS_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"value must be {SEARCH_SLOW_MS_MIN}-{SEARCH_SLOW_MS_MAX} ms",
        )
    set_setting(db, SEARCH_SLOW_MS_KEY, str(payload.value), user=user)
    return {"key": SEARCH_SLOW_MS_KEY, "value": get_setting(db, SEARCH_SLOW_MS_KEY)}