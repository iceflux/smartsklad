from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.modules.auth.dependencies import SESSION_COOKIE
from app.modules.auth.service import login, logout

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