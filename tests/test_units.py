from app.db import Base
from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.inventory.models import Unit
from app.modules.inventory.service import DEFAULT_UNITS, ensure_default_units


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


def test_units_table_registered():
    assert "units" in Base.metadata.tables


def test_default_units_seeded(db_session):
    assert db_session.query(Unit).count() == len(DEFAULT_UNITS)


def test_seed_is_idempotent(db_session):
    ensure_default_units(db_session)
    assert db_session.query(Unit).count() == len(DEFAULT_UNITS)


def test_default_units_contain_pieces_and_kilograms():
    assert "шт" in DEFAULT_UNITS
    assert "кг" in DEFAULT_UNITS
    assert len(DEFAULT_UNITS) == 34


def test_unit_is_soft_deletable(db_session):
    u = Unit(name="ящик личный")
    db_session.add(u)
    db_session.commit()
    u.is_deleted = True
    db_session.commit()
    assert db_session.query(Unit).filter(Unit.id == u.id).count() == 1
    assert db_session.query(Unit).count() == len(DEFAULT_UNITS) + 1
