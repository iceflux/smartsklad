from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db import get_db
from app.modules.audit.service import log_action
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import Permission
from app.modules.auth.service import (
    RoleNotFoundError,
    UserConflictError,
    UserPermissionError,
    create_user,
    update_user,
)

router = APIRouter()


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    is_active: bool
    role_id: int
    role_name: str


class UserRoleIn(BaseModel):
    role_id: int


class UserCreateIn(BaseModel):
    username: str
    email: str | None = None
    password: str
    role_id: int
    is_active: bool = True


class UserUpdateIn(BaseModel):
    username: str | None = None
    email: str | None = None
    password: str | None = None
    role_id: int | None = None
    is_active: bool | None = None


@router.get("/users", response_model=list[UserOut])
def api_users(db=Depends(get_db), user=Depends(require_permissions(Permission.users_manage))):
    users = db.query(User).order_by(User.id).all()
    return [UserOut(id=u.id, username=u.username, email=u.email, is_active=u.is_active,
                    role_id=u.role_id, role_name=u.role_name) for u in users]


@router.patch("/users/{user_id}/role")
def api_user_role(user_id: int, payload: UserRoleIn, request: Request, db=Depends(get_db), user=Depends(require_permissions(Permission.users_manage))):
    if user_id == user.id and not user.role.is_super:
        raise HTTPException(status_code=403, detail="Нельзя менять собственную роль")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    role = db.get(Role, payload.role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    if not user.role.is_super and (role.is_super or (target.role and target.role.is_super)):
        raise HTTPException(status_code=403, detail="Только администратор может менять супер-роли")
    old = target.role.name if target.role else None
    target.role_id = role.id
    db.commit()
    ip = request.client.host if request.client else None
    log_action(db, action="user.role.change", entity_type="user",
               entity_id=target.id, old_value=old, new_value=role.name,
               user_id=user.id, ip=ip)
    return {"id": target.id, "role_id": target.role_id}


@router.post("/users", status_code=201, response_model=UserOut)
def api_create_user(payload: UserCreateIn, db=Depends(get_db),
                    user=Depends(require_permissions(Permission.users_manage))):
    try:
        u = create_user(db, username=payload.username, email=payload.email,
                        password=payload.password, role_id=payload.role_id, actor=user,
                        is_active=payload.is_active)
    except UserPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except UserConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RoleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return UserOut(id=u.id, username=u.username, email=u.email, is_active=u.is_active,
                   role_id=u.role_id, role_name=u.role_name)


@router.patch("/users/{user_id}", response_model=UserOut)
def api_update_user(user_id: int, payload: UserUpdateIn, db=Depends(get_db),
                    user=Depends(require_permissions(Permission.users_manage))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    try:
        u = update_user(db, target, actor=user, username=payload.username, email=payload.email,
                        password=payload.password, role_id=payload.role_id, is_active=payload.is_active)
    except UserPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except UserConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RoleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return UserOut(id=u.id, username=u.username, email=u.email, is_active=u.is_active,
                   role_id=u.role_id, role_name=u.role_name)