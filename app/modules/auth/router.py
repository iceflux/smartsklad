from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.modules.auth.dependencies import SESSION_COOKIE, require_permissions
from app.modules.auth.models import User
from app.modules.auth.permissions import Permission
from app.modules.auth.service import (
    admin_reset_password,
    login,
    logout,
    request_password_reset,
    reset_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def api_login(body: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    ip = request.client.host if request.client else None
    session = login(db, body.username, body.password, ip)
    if session is None:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    response = Response(status_code=204)
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def api_logout(
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    if session_cookie:
        logout(db, session_cookie)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response


class RecoveryRequest(BaseModel):
    username_or_email: str


class ResetRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


@router.post("/password-recovery", status_code=202)
def api_recover(body: RecoveryRequest, db: Annotated[Session, Depends(get_db)]):
    request_password_reset(db, body.username_or_email)
    return {"ok": True}


@router.post("/reset-password")
def api_reset(body: ResetRequest, db: Annotated[Session, Depends(get_db)]):
    if not reset_password(db, body.token, body.new_password):
        raise HTTPException(status_code=400, detail="Неверная или устаревшая ссылка")
    return {"ok": True}


@router.post("/users/{user_id}/admin-reset")
def api_admin_reset(
    user_id: int,
    admin: Annotated[User, Depends(require_permissions(Permission.users_manage))],
    db: Annotated[Session, Depends(get_db)] = None,
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    temp = admin_reset_password(db, admin, target)
    return {"ok": True, "temporary_password": temp}