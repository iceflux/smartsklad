from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.permissions import Permission
from app.modules.importer.files import ImportFileError, list_payload_files, resolve_folder
from app.modules.importer.service import (
    import_path,
    plan_import,
    run_import,
    save_import_path,
)

router = APIRouter()
PERM = Depends(require_permissions(Permission.import_manage))


class FilesIn(BaseModel):
    filenames: list[str]
    unit_map: dict[str, str] = {}


class PathIn(BaseModel):
    path: str


def _folder(db: Session):
    return resolve_folder(import_path(db))


@router.get("/import/files")
def api_files(db=Depends(get_db), user=PERM):
    folder = _folder(db)
    return {"folder": str(folder), "files": list_payload_files(folder)}


@router.post("/import/preview")
def api_preview(payload: FilesIn, db=Depends(get_db), user=PERM):
    try:
        return plan_import(db, _folder(db), payload.filenames, unit_map=payload.unit_map)
    except ImportFileError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post("/import/run")
def api_run(payload: FilesIn, db=Depends(get_db), user=PERM):
    try:
        return run_import(db, _folder(db), payload.filenames, user=user, unit_map=payload.unit_map)
    except ImportFileError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.put("/import/path")
def api_set_path(payload: PathIn, db=Depends(get_db), user=PERM):
    try:
        save_import_path(db, payload.path, user=user)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"ok": True}
