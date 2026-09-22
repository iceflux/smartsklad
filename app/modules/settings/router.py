from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import get_current_user, require_permissions
from app.modules.auth.permissions import Permission
from app.modules.settings.models import Setting
from app.modules.settings.service import STOCK_TRACK_DEFAULT_KEY, get_setting, set_setting

router = APIRouter()


class StockTrackIn(BaseModel):
    value: str


def _all_settings(db):
    rows = db.query(Setting).order_by(Setting.key).all()
    return [{"key": r.key, "value": r.value} for r in rows]


@router.get("/settings")
def api_get_settings(user=Depends(get_current_user), db=Depends(get_db)):
    return _all_settings(db)


@router.put("/settings/stock-track")
def api_set_stock_track(payload: StockTrackIn, user=Depends(require_permissions(Permission.settings_change)), db=Depends(get_db)):
    if payload.value not in ("true", "false"):
        raise HTTPException(status_code=422, detail="value must be 'true' or 'false'")
    set_setting(db, STOCK_TRACK_DEFAULT_KEY, payload.value, user=user)
    return {"key": STOCK_TRACK_DEFAULT_KEY, "value": get_setting(db, STOCK_TRACK_DEFAULT_KEY)}