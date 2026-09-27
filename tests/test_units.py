from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS


def test_units_permission_exists():
    assert Permission.units_manage.value == "units.manage"


def test_units_permission_has_label():
    assert PERMISSION_LABELS["units.manage"] == "Единицы измерения"


def test_page_context_exposes_units_flag():
    from app.modules.auth.dependencies import page_context
    from app.modules.auth.models import Role, User

    user = User(username="boss", role=Role(name="admin", permissions=[], is_super=True))
    assert page_context(user)["can_manage_units"] is True
