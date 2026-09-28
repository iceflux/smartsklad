from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_db
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.permissions import Permission
from app.modules.inventory.service import create_unit, delete_unit, list_units, update_unit

router = APIRouter()
PERM = Depends(require_permissions(Permission.units_manage))
READ = Depends(require_permissions(Permission.products_view))


class UnitIn(BaseModel):
    name: str


def _out(unit) -> dict:
    return {"id": unit.id, "name": unit.name}


@router.get("/units")
def api_list(db=Depends(get_db), user=READ):
    return [_out(u) for u in list_units(db)]


@router.post("/units")
def api_create(payload: UnitIn, db=Depends(get_db), user=PERM):
    try:
        return _out(create_unit(db, payload.name, user=user))
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.patch("/units/{unit_id}")
def api_update(unit_id: int, payload: UnitIn, db=Depends(get_db), user=PERM):
    try:
        return _out(update_unit(db, unit_id, payload.name, user=user))
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.delete("/units/{unit_id}")
def api_delete(unit_id: int, db=Depends(get_db), user=PERM):
    try:
        delete_unit(db, unit_id, user=user)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"ok": True}
