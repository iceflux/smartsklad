from app.modules.auth.models import Permission, Role, User
from app.modules.auth.service import ensure_default_roles, get_role_by_name
from app.modules.auth.dependencies import user_has_permission


def test_permission_codes():
    codes = [p.value for p in Permission]
    assert "products.view" in codes and "audit.view" in codes and "users.manage" in codes
    assert len(codes) == 20


def test_ensure_default_roles_creates_four(db_session):
    ensure_default_roles(db_session)
    assert db_session.query(Role).count() == 4
    admin = get_role_by_name(db_session, "admin")
    assert admin is not None and admin.is_super
    assert get_role_by_name(db_session, "manager").permissions == [
        "products.view", "price.change",
        "orders.view", "orders.create", "orders.edit", "orders.delete", "orders.status",
        "customers.manage", "analytics.view", "reports.export",
        "notifications.send", "notifications.view",
    ]


def test_user_has_permission(db_session):
    ensure_default_roles(db_session)
    u = User(
        username="boss",
        password_hash="x",
        role_id=get_role_by_name(db_session, "admin").id,
    )
    u.role = get_role_by_name(db_session, "admin")
    assert user_has_permission(u, Permission.audit_view)
    u2 = User(
        username="worker",
        password_hash="x",
        role_id=get_role_by_name(db_session, "warehouseman").id,
    )
    u2.role = get_role_by_name(db_session, "warehouseman")
    assert user_has_permission(u2, Permission.stock_change)
    assert not user_has_permission(u2, Permission.audit_view)