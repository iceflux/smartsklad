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


def test_units_flag_follows_units_permission():
    from app.modules.auth.dependencies import page_context
    from app.modules.auth.models import Role, User

    allowed = User(username="a", role=Role(name="r1", permissions=["units.manage"], is_super=False))
    denied = User(username="b", role=Role(name="r2", permissions=["audit.view"], is_super=False))
    assert page_context(allowed)["can_manage_units"] is True
    assert page_context(denied)["can_manage_units"] is False
