import pytest
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.importer.models import ImportMap


def test_import_permission_exists():
    assert Permission.import_manage.value == "import.manage"


def test_import_permission_has_label():
    assert PERMISSION_LABELS["import.manage"] == "Импорт данных из 1С"


def test_import_map_blocks_duplicate_source(db_session):
    db_session.add(ImportMap(entity_type="product", source_id="a", entity_id=1))
    db_session.commit()
    db_session.add(ImportMap(entity_type="product", source_id="a", entity_id=2))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_import_map_allows_same_source_for_other_type(db_session):
    db_session.add(ImportMap(entity_type="product", source_id="a", entity_id=1))
    db_session.commit()
    db_session.add(ImportMap(entity_type="customer", source_id="a", entity_id=2))
    db_session.commit()
    assert db_session.query(ImportMap).count() == 2


def test_import_map_keeps_source_label(db_session):
    db_session.add(ImportMap(entity_type="order", source_id="o1", entity_id=7,
                            source_label="0000-000001"))
    db_session.commit()
    row = db_session.query(ImportMap).one()
    assert row.source_label == "0000-000001"


def test_import_map_table_registered():
    assert "import_map" in Base.metadata.tables
