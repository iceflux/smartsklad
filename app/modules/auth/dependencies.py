from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.auth.models import Role, User
from app.modules.auth.service import get_user_by_token

SESSION_COOKIE = "session"


def get_current_user(
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> User:
    if session_cookie is None:
        raise HTTPException(status_code=401, detail="Не авторизован")
    user = get_user_by_token(db, session_cookie)
    if user is None:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return user


def require_roles(*roles: Role):
    def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return checker