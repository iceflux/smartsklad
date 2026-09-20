from app.db import Base
from app.modules.auth.models import Role


def test_role_enum():
    assert [r.value for r in Role] == ["admin", "seller", "warehouseman", "manager"]


def test_models_tables_registered():
    assert "users" in Base.metadata.tables
    assert "sessions" in Base.metadata.tables