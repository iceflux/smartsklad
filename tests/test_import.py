import json

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.modules.auth.models import User
from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.auth.service import get_role_by_name, hash_password
from app.modules.customers.models import Customer
from app.modules.customers.service import _validate_inn, create_customer
from app.modules.importer.files import (
    ImportFileError,
    list_payload_files,
    read_payloads,
    resolve_folder,
)
from app.modules.importer.models import ImportMap

PASSWORD = "password123"


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


def _admin(db_session):
    user = User(username="boss", password_hash=hash_password(PASSWORD),
                role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.mark.parametrize("inn", ["7700000000", "770000000000"])
def test_inn_accepts_legal_and_individual(inn):
    assert _validate_inn(inn) == inn


def test_inn_rejects_wrong_length():
    with pytest.raises(ValueError):
        _validate_inn("123")


def test_inn_empty_is_none():
    assert _validate_inn(None) is None
    assert _validate_inn("   ") is None


def test_customer_accepts_individual_inn(db_session):
    user = _admin(db_session)
    customer = create_customer(db_session, name="ИП Иванов", inn="770000000000", user=user)
    assert customer.inn == "770000000000"


def _write(tmp_path, name, payload, bom=True):
    path = tmp_path / name
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)
    return name


def test_reads_bom_single_line_file(tmp_path):
    name = _write(tmp_path, "export.json", {"products": [{"id": "p1", "name": "Гвоздь"}]})
    sections = read_payloads(tmp_path, [name])
    assert sections["products"][0]["name"] == "Гвоздь"
    assert sections["customers"] == []
    assert sections["orders"] == []


def test_merges_three_separate_files(tmp_path):
    a = _write(tmp_path, "clients.json", {"customers": [{"id": "c1", "name": "ООО Ромашка"}]})
    b = _write(tmp_path, "goods.json", {"products": [{"id": "p1", "name": "Гвоздь"}]})
    c = _write(tmp_path, "orders.json", {"orders": [{"id": "o1", "items": []}]})
    sections = read_payloads(tmp_path, [a, b, c])
    assert len(sections["customers"]) == 1
    assert len(sections["products"]) == 1
    assert len(sections["orders"]) == 1


def test_rejects_broken_json(tmp_path):
    (tmp_path / "bad.json").write_bytes(b"\xef\xbb\xbf{not json")
    with pytest.raises(ImportFileError) as err:
        read_payloads(tmp_path, ["bad.json"])
    assert "bad.json" in str(err.value)


def test_rejects_array_root(tmp_path):
    name = _write(tmp_path, "list.json", [1, 2, 3])
    with pytest.raises(ImportFileError):
        read_payloads(tmp_path, [name])


def test_rejects_section_that_is_not_list(tmp_path):
    name = _write(tmp_path, "odd.json", {"products": {"id": 1}})
    with pytest.raises(ImportFileError):
        read_payloads(tmp_path, [name])


def test_rejects_path_traversal(tmp_path):
    with pytest.raises(ImportFileError):
        read_payloads(tmp_path, ["../secret.json"])


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(ImportFileError):
        read_payloads(tmp_path, ["nope.json"])


def test_empty_selection_is_error(tmp_path):
    with pytest.raises(ImportFileError):
        read_payloads(tmp_path, [])


def test_lists_json_files_only(tmp_path):
    _write(tmp_path, "a.json", {"products": []})
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    names = [f["name"] for f in list_payload_files(tmp_path)]
    assert names == ["a.json"]


def test_resolve_folder_makes_relative_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "folder").mkdir()
    assert resolve_folder("folder").is_absolute()
    assert resolve_folder(None).is_absolute()


@pytest.mark.parametrize("name", ["../secret.json", "..\\secret.json", "sub/secret.json"])
def test_traversal_cannot_reach_existing_file_outside_folder(tmp_path, name):
    (tmp_path / "secret.json").write_bytes(b'{"products": [{"id": "leak"}]}')
    folder = tmp_path / "folder"
    folder.mkdir()
    with pytest.raises(ImportFileError):
        read_payloads(folder, [name])


def test_rejects_directory_instead_of_file(tmp_path):
    (tmp_path / "folder.json").mkdir()
    with pytest.raises(ImportFileError):
        read_payloads(tmp_path, ["folder.json"])


def test_ignores_unknown_top_level_keys(tmp_path):
    name = _write(tmp_path, "mixed.json", {
        "meta": {"exported": "2026-01-01"},
        "products": [{"id": "p1"}],
    })
    sections = read_payloads(tmp_path, [name])
    assert sections == {"products": [{"id": "p1"}], "customers": [], "orders": []}
