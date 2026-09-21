from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

from app.db import get_db
from app.modules.audit.service import log_action
from app.modules.auth.dependencies import require_permissions
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import Permission
from app.modules.auth.utils import parse_permissions

router = APIRouter()

PERMISSION_LABELS = {
    "products.view": "Просмотр товаров",
    "products.edit": "Создание и изменение товаров",
    "stock.change": "Изменение остатков",
    "price.change": "Изменение цен",
    "categories.manage": "Управление категориями",
    "orders.view": "Просмотр заказов",
    "orders.create": "Создание заказов",
    "orders.edit": "Редактирование заказов",
    "orders.delete": "Удаление заказов",
    "orders.status": "Смена статусов заказов",
    "customers.manage": "Управление контактами",
    "analytics.view": "Просмотр аналитики",
    "reports.export": "Экспорт отчётов",
    "users.manage": "Управление пользователями и ролями",
    "backup.manage": "Бэкапы и восстановление",
    "audit.view": "Просмотр журнала аудита",
    "settings.change": "Изменение настроек",
}


class RoleOut(BaseModel):
    id: int
    name: str
    is_system: bool
    is_super: bool
    permissions: list[str]
    user_count: int


class RoleIn(BaseModel):
    name: str
    permissions: list[str] = []


@router.get("/roles", response_model=list[RoleOut])
def api_roles(db=Depends(get_db), user=Depends(require_permissions(Permission.users_manage))):
    roles = db.query(Role).order_by(Role.id).all()
    counts = dict(db.query(User.role_id, func.count(User.id)).group_by(User.role_id).all())
    return [
        RoleOut(
            id=r.id, name=r.name, is_system=r.is_system, is_super=r.is_super,
            permissions=r.permissions or [], user_count=counts.get(r.id, 0),
        )
        for r in roles
    ]


@router.post("/roles", status_code=201)
def api_create_role(payload: RoleIn, db=Depends(get_db), user=Depends(require_permissions(Permission.users_manage))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название роли не может быть пустым")
    if db.query(Role).filter(Role.name == name).first():
        raise HTTPException(status_code=409, detail="Роль с таким именем уже существует")
    try:
        perms = parse_permissions(payload.permissions)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    role = Role(name=name, is_system=False, is_super=False, permissions=perms)
    db.add(role)
    db.commit()
    db.refresh(role)
    result = {"id": role.id, "name": role.name}
    log_action(db, action="role.create", entity_type="role", entity_id=role.id, new_value=role.name, user_id=user.id)
    return result


@router.patch("/roles/{role_id}")
def api_update_role(role_id: int, payload: RoleIn, db=Depends(get_db), user=Depends(require_permissions(Permission.users_manage))):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название роли не может быть пустым")
    if role.is_system and name != role.name:
        raise HTTPException(status_code=409, detail="Системные роли нельзя переименовывать")
    if db.query(Role).filter(Role.name == name, Role.id != role_id).first():
        raise HTTPException(status_code=409, detail="Роль с таким именем уже существует")
    try:
        perms = parse_permissions(payload.permissions)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    role.name = name
    role.permissions = perms
    db.commit()
    db.refresh(role)
    result = {"id": role.id, "name": role.name, "permissions": role.permissions}
    log_action(db, action="role.update", entity_type="role", entity_id=role.id, new_value=role.name, user_id=user.id)
    return result


@router.delete("/roles/{role_id}", status_code=204)
def api_delete_role(role_id: int, db=Depends(get_db), user=Depends(require_permissions(Permission.users_manage))):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    if role.is_system:
        raise HTTPException(status_code=409, detail="Системную роль нельзя удалить")
    users = db.query(User).filter(User.role_id == role_id).all()
    if users:
        raise HTTPException(status_code=409, detail=f"Роль назначена {len(users)} пользователю(ям)")
    old_name = role.name
    db.delete(role)
    db.commit()
    log_action(db, action="role.delete", entity_type="role", entity_id=role_id, old_value=old_name, user_id=user.id)