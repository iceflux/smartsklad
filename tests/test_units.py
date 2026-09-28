import pytest

from app.db import Base
from app.modules.auth.models import User
from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory.models import Product, Unit
from app.modules.inventory.service import (
    DEFAULT_UNITS,
    create_product,
    create_unit,
    delete_unit,
    ensure_default_units,
    ensure_unit,
    get_unit_by_name,
    list_units,
    update_unit,
)

PASSWORD = "password123"


def _ensure_user(db_session):
    user = db_session.query(User).filter(User.username == "boss").first()
    if user is None:
        user = User(username="boss", password_hash=hash_password(PASSWORD),
                    role_id=get_role_by_name(db_session, "admin").id)
        db_session.add(user)
        db_session.commit()
    return user


def _admin(db_session):
    user = db_session.query(User).filter(User.username == "boss").first()
    if user is None:
        user = User(username="boss", password_hash=hash_password(PASSWORD),
                    role_id=get_role_by_name(db_session, "admin").id)
        db_session.add(user)
        db_session.commit()
    return user


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
    unit = create_unit(db_session, "ящик личный")
    delete_unit(db_session, unit.id)
    db_session.refresh(unit)
    assert unit.is_deleted is True
    assert db_session.query(Unit).filter(Unit.id == unit.id).count() == 1
    assert db_session.query(Unit).count() == len(DEFAULT_UNITS) + 1


def test_list_units_excludes_soft_deleted(db_session):
    dropped = create_unit(db_session, "Килограмм")
    db_session.get(Unit, dropped.id).is_deleted = True
    db_session.commit()
    names = [u.name for u in list_units(db_session)]
    assert "Килограмм" not in names
    assert "кг" in names


def test_get_unit_by_name_is_case_and_space_insensitive(db_session):
    found = get_unit_by_name(db_session, "  кг ")
    assert found.name == "кг"
    assert get_unit_by_name(db_session, "КГ").id == found.id
    assert get_unit_by_name(db_session, "ящик-тест") is None


def test_create_unit_rejects_duplicate_and_empty(db_session):
    create_unit(db_session, "Килограмм")
    with pytest.raises(ValueError):
        create_unit(db_session, "килограмм")
    with pytest.raises(ValueError):
        create_unit(db_session, "кг")
    with pytest.raises(ValueError):
        create_unit(db_session, "   ")


def test_create_unit_accepts_name_at_limit_and_rejects_longer(db_session):
    at_limit = "я" * 32
    assert create_unit(db_session, at_limit).name == at_limit
    with pytest.raises(ValueError):
        create_unit(db_session, "я" * 33)


def test_ensure_unit_creates_once(db_session):
    first = ensure_unit(db_session, "ящик-тест")
    second = ensure_unit(db_session, "Ящик-Тест ")
    assert first.id == second.id


def test_update_unit_renames(db_session):
    unit = create_unit(db_session, "Килограмм")
    update_unit(db_session, unit.id, "кг2")
    assert db_session.get(Unit, unit.id).name == "кг2"


def test_update_unit_accepts_name_at_limit_and_rejects_longer(db_session):
    unit = create_unit(db_session, "Килограмм-длина")
    at_limit = "ф" * 32
    update_unit(db_session, unit.id, at_limit)
    assert db_session.get(Unit, unit.id).name == at_limit
    with pytest.raises(ValueError):
        update_unit(db_session, unit.id, "ф" * 33)
    assert db_session.get(Unit, unit.id).name == at_limit


def test_rename_allows_reusing_a_freed_name(db_session):
    unit = create_unit(db_session, "Килограмм")
    delete_unit(db_session, unit.id)
    again = create_unit(db_session, "Килограмм")
    assert again.name == "Килограмм"


def test_delete_unit_refuses_when_product_uses_it(db_session):
    from app.modules.inventory.models import Product
    unit = get_unit_by_name(db_session, "кг")
    product = Product(name="Гвоздь", sku="S1", unit_id=unit.id, price=0.0, rest=0)
    db_session.add(product)
    db_session.commit()
    with pytest.raises(ValueError):
        delete_unit(db_session, unit.id)
    assert db_session.get(Unit, unit.id).is_deleted is False


def test_delete_unit_refuses_for_soft_deleted_product(db_session):
    from app.modules.inventory.models import Product
    unit = get_unit_by_name(db_session, "кг")
    product = Product(name="Гвоздь", sku="S1", unit_id=unit.id, price=0.0, rest=0, is_deleted=True)
    db_session.add(product)
    db_session.commit()
    with pytest.raises(ValueError):
        delete_unit(db_session, unit.id)
    assert db_session.get(Unit, unit.id).is_deleted is False


def test_delete_unit_marks_deleted(db_session):
    unit = create_unit(db_session, "Грамм")
    delete_unit(db_session, unit.id)
    assert db_session.get(Unit, unit.id).is_deleted is True


def _product_with_unit(db_session, unit_name="Килограмм-тест"):
    unit = create_unit(db_session, unit_name)
    return create_product(db_session, name="Гвоздь", sku=f"S-{unit_name}",
                          category_id=None, unit_id=unit.id,
                          price=10.0, characteristic=None, rest=5)


def test_product_unit_property_returns_name(db_session):
    product = _product_with_unit(db_session)
    db_session.refresh(product)
    assert product.unit == "Килограмм-тест"


def test_product_unit_property_without_loaded_relation(db_session):
    unit = create_unit(db_session, "Грамм-тест")
    product = Product(name="Гвоздь", sku="S1", unit_id=unit.id, price=0.0, rest=0)
    assert product.unit == ""


def test_create_product_rejects_unknown_unit_id(db_session):
    with pytest.raises(ValueError):
        create_product(db_session, name="Гвоздь", sku="S9", category_id=None, unit_id=9999,
                       price=0.0, characteristic=None, rest=0)


def test_create_product_without_unit_id_falls_back_to_pieces(db_session):
    product = create_product(db_session, name="Гвоздь", sku="S10", category_id=None, unit_id=None,
                             price=0.0, characteristic=None, rest=0)
    db_session.refresh(product)
    assert product.unit == "шт"


def test_product_form_offers_unit_select(client, db_session):
    _ensure_user(db_session)
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)
    response = client.get("/products/new")
    assert response.status_code == 200
    assert 'name="unit_id"' in response.text
    assert 'name="unit"' not in response.text

