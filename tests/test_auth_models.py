from app.db import Base


def test_models_tables_registered():
    assert "users" in Base.metadata.tables
    assert "sessions" in Base.metadata.tables
    assert "roles" in Base.metadata.tables