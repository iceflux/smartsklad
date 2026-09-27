import json
import os
import sys

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

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
from app.modules.importer.orders import parse_order_date, resolve_orders
from app.modules.importer.resolve import (
    SkuFactory,
    _unit,
    clean_inn,
    norm,
    resolve_customers,
    resolve_products,
    resolve_service_customer,
)
from app.modules.inventory.models import Product
from app.modules.orders.service import create_order, next_order_number

if sys.platform.startswith("win32"):
    import msvcrt

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


def test_rejects_section_of_non_dict_rows(tmp_path):
    name = _write(tmp_path, "rows.json", {"products": [1, 2]})
    with pytest.raises(ImportFileError) as err:
        read_payloads(tmp_path, [name])
    assert "products" in str(err.value)


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
    with pytest.raises(ImportFileError) as err:
        read_payloads(folder, [name])
    assert "не должно содержать путь" in str(err.value)


def test_rejects_symlink_pointing_outside_folder(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    secret = _write(tmp_path, "secret.json", {"products": [{"id": "leak"}]})
    try:
        os.symlink(tmp_path / secret, folder / "link.json")
    except OSError as err:
        pytest.skip(f"symlinks unavailable: {err}")
    with pytest.raises(ImportFileError) as err:
        read_payloads(folder, ["link.json"])
    assert "за пределами папки импорта" in str(err.value)


@pytest.mark.skipif(not sys.platform.startswith("win32"), reason="msvcrt locking is Windows-only")
def test_reports_locked_file_instead_of_crashing(tmp_path):
    name = _write(tmp_path, "locked.json", {"products": []})
    with open(tmp_path / name, "r+b") as handle:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            with pytest.raises(ImportFileError) as err:
                read_payloads(tmp_path, [name])
            assert name in str(err.value)
        finally:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


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


def _product(db_session, name, sku, unit="шт", rest=0):
    p = Product(name=name, sku=sku, unit=unit, price=0.0, rest=rest)
    db_session.add(p)
    db_session.commit()
    return p


def test_norm_trims_and_lowercases():
    assert norm("  ГВОЗДЬ  ") == "гвоздь"
    assert norm(None) == ""


def test_clean_inn_rules():
    assert clean_inn("7700000000") == "7700000000"
    assert clean_inn("770000000000") == "770000000000"
    assert clean_inn("123") is None
    assert clean_inn(None) is None


@pytest.mark.parametrize("value, expected", [
    (None, "шт"),
    ("", "шт"),
    ("   ", "шт"),
    (10, "шт"),
    ("шт", "шт"),
    ("  короб ", "короб"),
])
def test_unit_normalisation(value, expected):
    assert _unit(value) == expected


def test_product_plan_unit_falls_back_when_blank(db_session):
    plans, _ = resolve_products(db_session, [{"id": "p1", "name": "Гвоздь", "unit": "   "}])
    assert plans[0].unit == "шт"


def test_product_matched_by_name_ignoring_case_and_spaces(db_session):
    existing = _product(db_session, "Гвоздь", "S1")
    _product(db_session, "Другое имя", "S2")
    plans, stats = resolve_products(db_session, [{"id": "p1", "name": "  гвоздь ", "unit": "шт"}])
    assert len(plans) == 1
    assert plans[0].create is False
    assert plans[0].existing_id == existing.id
    assert stats["matched"] == 1
    assert stats["created"] == 0


def test_product_created_with_generated_sku(db_session):
    plans, stats = resolve_products(db_session, [{"id": "p1", "name": "Гвоздь", "unit": "шт"}])
    assert plans[0].create is True
    assert plans[0].sku == "1C-00001"
    assert stats["created"] == 1


def test_generated_sku_skips_taken_one(db_session):
    _product(db_session, "Чужой", "1C-00001")
    plans, _ = resolve_products(db_session, [{"id": "p1", "name": "Гвоздь", "unit": "шт"}])
    assert plans[0].sku == "1C-00002"


def test_generated_sku_skips_soft_deleted_product(db_session):
    p = _product(db_session, "Чужой", "1C-00001")
    p.is_deleted = True
    db_session.commit()
    plans, _ = resolve_products(db_session, [{"id": "p1", "name": "Гвоздь", "unit": "шт"}])
    assert plans[0].sku == "1C-00002"


def test_sku_factory_is_reusable(db_session):
    _product(db_session, "Чужой", "1C-00001")
    factory = SkuFactory(db_session)
    first = factory()
    second = factory()
    assert first == "1C-00002"
    assert second == "1C-00003"


def test_soft_deleted_product_is_not_matched(db_session):
    p = _product(db_session, "Гвоздь", "S1")
    p.is_deleted = True
    db_session.commit()
    plans, _ = resolve_products(db_session, [{"id": "p1", "name": "Гвоздь", "unit": "шт"}])
    assert plans[0].create is True


def test_import_map_wins_over_name(db_session):
    by_name = _product(db_session, "Гвоздь", "S1")
    mapped = _product(db_session, "Другое имя", "S2")
    db_session.add(ImportMap(entity_type="product", source_id="p1", entity_id=mapped.id))
    db_session.commit()
    plans, stats = resolve_products(db_session, [{"id": "p1", "name": "Гвоздь", "unit": "шт"}])
    assert plans[0].existing_id == mapped.id
    assert plans[0].existing_id != by_name.id
    assert stats["matched"] == 1
    assert stats["created"] == 0


@pytest.mark.parametrize("row", [
    {"name": "Гвоздь", "unit": "шт"},
    {"id": "p1", "unit": "шт"},
    {"id": "   ", "name": "Гвоздь", "unit": "шт"},
    {"id": "p1", "name": "   ", "unit": "шт"},
])
def test_row_without_id_or_name_is_skipped(db_session, row):
    plans, stats = resolve_products(db_session, [row])
    assert plans == []
    assert stats["skipped"] == 1


def test_same_name_in_one_file_creates_both(db_session):
    rows = [{"id": "p1", "name": "Гвоздь", "unit": "шт"},
            {"id": "p2", "name": "  гвоздь ", "unit": "шт"}]
    plans, stats = resolve_products(db_session, rows)
    assert [p.source_id for p in plans] == ["p1", "p2"]
    assert [p.create for p in plans] == [True, True]
    assert [p.sku for p in plans] == ["1C-00001", "1C-00002"]
    assert stats["created"] == 2
    assert stats["matched"] == 0


def test_customer_matched_by_name(db_session):
    db_session.add(Customer(name="ООО Ромашка"))
    db_session.commit()
    plans, stats = resolve_customers(db_session, [{"id": "c1", "name": "ооо ромашка"}])
    assert plans[0].create is False
    assert stats["matched"] == 1


def test_customer_created_and_bad_inn_dropped(db_session):
    plans, stats = resolve_customers(db_session, [{"id": "c1", "name": "ИП Иванов", "inn": "123"}])
    assert plans[0].create is True
    assert plans[0].inn is None
    assert stats["inn_dropped"] == 1


def test_customer_import_map_wins_over_name(db_session):
    by_name = Customer(name="ООО Ромашка")
    other = Customer(name="ООО Одуванчик")
    db_session.add_all([by_name, other])
    db_session.commit()
    db_session.add(ImportMap(entity_type="customer", source_id="c1", entity_id=other.id))
    db_session.commit()
    plans, stats = resolve_customers(db_session, [{"id": "c1", "name": "ООО Ромашка"}])
    assert plans[0].existing_id == other.id
    assert plans[0].create is False
    assert stats["matched"] == 1
    assert stats["created"] == 0


def test_product_map_does_not_cover_customer_source_id(db_session):
    product = _product(db_session, "Гвоздь", "S1")
    db_session.add(ImportMap(entity_type="product", source_id="c1", entity_id=product.id))
    db_session.commit()
    plans, stats = resolve_customers(db_session, [{"id": "c1", "name": "ООО Ромашка"}])
    assert plans[0].create is True
    assert plans[0].existing_id is None
    assert stats["created"] == 1
    assert stats["matched"] == 0


def test_soft_deleted_customer_is_not_matched(db_session):
    db_session.add(Customer(name="ООО Ромашка", is_deleted=True))
    db_session.commit()
    plans, _ = resolve_customers(db_session, [{"id": "c1", "name": "ООО Ромашка"}])
    assert plans[0].create is True


def test_service_customer_planned_once(db_session):
    plan = resolve_service_customer(db_session)
    assert plan is not None
    assert plan.name == "Без контрагента (1С)"
    assert plan.create is True


def test_service_customer_matched_when_it_exists(db_session):
    existing = Customer(name="Без контрагента (1С)")
    db_session.add(existing)
    db_session.commit()
    plan = resolve_service_customer(db_session)
    assert plan.create is False
    assert plan.existing_id == existing.id


DATE = "2017-12-02 21:22:39"


def _customer(db_session, name="ООО Ромашка"):
    c = Customer(name=name)
    db_session.add(c)
    db_session.commit()
    return c


def _order(source_id="o1", number="1", date=DATE, customer_id="c1", items=()):
    return {"id": source_id, "number": number, "date": date,
            "customer_id": customer_id, "items": list(items)}


def _line(name="Гвоздь", product_id="p1", quantity=1, price=10.0, unit="шт"):
    return {"product_id": product_id, "name": name, "unit": unit,
            "quantity": quantity, "price": price}


def test_parse_both_date_formats():
    a = parse_order_date("2017-12-02 21:22:39")
    b = parse_order_date("2017-12-02T21:22:39")
    assert a == b
    assert a.tzinfo is not None


def test_order_uses_app_numbering_and_done_status(db_session):
    customer = _customer(db_session)
    _product(db_session, "Гвоздь", "S1", rest=100)
    order = {"id": "o1", "number": "0000-000001", "date": "2017-12-02 21:22:39",
             "customer_id": None, "customer_name": None,
             "items": [{"product_id": None, "name": "Гвоздь", "unit": "шт",
                        "quantity": 2, "price": 10.0}]}
    plans, stats = resolve_orders(db_session, [order], {}, {})
    assert len(plans) == 1
    assert stats["created"] == 1
    assert plans[0].status == "done"
    assert plans[0].number == next_order_number(db_session)
    assert plans[0].source_number == "0000-000001"
    assert plans[0].customer_id is None
    assert stats["service_customer_planned"] == 1


def test_order_discount_taken_from_customer(db_session):
    customer = _customer(db_session)
    customer.personal_discount = 10.0
    db_session.commit()
    _product(db_session, "Гвоздь", "S1", rest=100)
    order = {"id": "o1", "number": "1", "date": "2017-12-02 21:22:39", "customer_id": "c1",
             "items": [{"product_id": "p1", "name": "Гвоздь", "unit": "шт",
                        "quantity": 3, "price": 10.0}]}
    plans, _ = resolve_orders(db_session, [order], {"p1": 1}, {"c1": customer.id})
    assert plans[0].discount == 10.0
    assert plans[0].items[0].product_name == "Гвоздь"


def test_duplicate_product_lines_are_merged(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь", "S1", rest=100)
    order = {"id": "o1", "number": "1", "date": "2017-12-02 21:22:39", "customer_id": "c1",
             "items": [
                 {"product_id": "p1", "name": "Гвоздь", "unit": "шт", "quantity": 2, "price": 10.0},
                 {"product_id": "p1", "name": "Гвоздь", "unit": "шт", "quantity": 3, "price": 10.0}]}
    plans, stats = resolve_orders(db_session, [order], {"p1": product.id}, {"c1": customer.id})
    assert len(plans[0].items) == 1
    assert plans[0].items[0].quantity == 5
    assert stats["items_merged"] == 1


def test_empty_order_is_kept_and_counted(db_session):
    customer = _customer(db_session)
    order = {"id": "o1", "number": "1", "date": "2017-12-02 21:22:39",
             "customer_id": "c1", "items": []}
    plans, stats = resolve_orders(db_session, [order], {}, {"c1": customer.id})
    assert plans[0].items == []
    assert stats["empty_orders"] == 1


def test_unresolvable_item_skips_order(db_session):
    customer = _customer(db_session)
    order = {"id": "o1", "number": "1", "date": "2017-12-02 21:22:39", "customer_id": "c1",
             "items": [{"product_id": "zzz", "name": "", "unit": "шт",
                        "quantity": 1, "price": 1.0}]}
    plans, stats = resolve_orders(db_session, [order], {}, {"c1": customer.id})
    assert plans == []
    assert stats["skipped"] == 1
    assert stats["created"] == 0
    assert any("позиция" in w for w in stats["warnings"])


def test_item_without_product_id_uses_name(db_session):
    customer = _customer(db_session)
    order = {"id": "o1", "number": "1", "date": "2017-12-02 21:22:39", "customer_id": "c1",
             "items": [{"product_id": "unknown-1", "name": "Саморез", "unit": "шт",
                        "quantity": 4, "price": 5.0}]}
    plans, stats = resolve_orders(db_session, [order], {}, {"c1": customer.id})
    assert plans[0].items[0].name == "Саморез"
    assert plans[0].items[0].product_id is None
    assert stats["products_from_items"] == 1


def test_repeated_source_id_is_skipped(db_session):
    customer = _customer(db_session)
    order = {"id": "o1", "number": "1", "date": "2017-12-02 21:22:39",
             "customer_id": "c1", "items": []}
    plans, stats = resolve_orders(db_session, [order, dict(order)], {}, {"c1": customer.id})
    assert len(plans) == 1
    assert stats["skipped"] == 1


@pytest.mark.parametrize("bad, shown", [
    (None, ""), ("", ""), ("   ", ""),
    ("02.12.2017", "02.12.2017"), ("2017-12-02", "2017-12-02"),
])
def test_unparseable_date_skips_order(db_session, bad, shown):
    customer = _customer(db_session)
    plans, stats = resolve_orders(db_session, [_order(date=bad)], {}, {"c1": customer.id})
    assert plans == []
    assert stats["skipped"] == 1
    assert stats["warnings"] == [f"не удалось разобрать дату заказа: {shown!r}"]


def test_row_without_id_is_skipped(db_session):
    plans, stats = resolve_orders(db_session, [{"number": "1", "date": DATE, "items": []}], {}, {})
    assert plans == []
    assert stats["skipped"] == 1


def test_order_keeps_file_date(db_session):
    customer = _customer(db_session)
    plans, _ = resolve_orders(db_session, [_order(date="2017-12-02T21:22:39")], {},
                              {"c1": customer.id})
    assert plans[0].created_at == parse_order_date("2017-12-02T21:22:39")


def test_orders_get_consecutive_numbers(db_session):
    customer = _customer(db_session)
    rows = [_order(source_id="o1"), _order(source_id="o2", number="2")]
    plans, _ = resolve_orders(db_session, rows, {}, {"c1": customer.id})
    assert [p.number for p in plans] == ["00001", "00002"]


def test_numbering_continues_after_existing_orders(db_session):
    customer = _customer(db_session)
    create_order(db_session, customer_id=customer.id, items=[])
    plans, _ = resolve_orders(db_session, [_order()], {}, {"c1": customer.id})
    assert plans[0].number == "00002"


def test_snapshots_come_from_database_product_not_file(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь 50мм", "S1", unit="короб")
    plans, _ = resolve_orders(db_session, [_order(items=[_line(name="  гвоздь ", unit="шт")])],
                              {"p1": product.id}, {"c1": customer.id})
    item = plans[0].items[0]
    assert item.product_name == "Гвоздь 50мм"
    assert item.product_unit == "короб"
    assert item.name == "гвоздь"
    assert item.unit == "шт"
    assert item.price == 10.0


def test_unmapped_line_resolves_product_by_name(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь 50мм", "S1", unit="короб")
    plans, stats = resolve_orders(
        db_session, [_order(items=[_line(name="  ГВОЗДЬ 50ММ ", product_id="missing-1")])],
        {}, {"c1": customer.id})
    item = plans[0].items[0]
    assert item.product_id == product.id
    assert item.product_name == "Гвоздь 50мм"
    assert item.product_unit == "короб"
    assert stats["products_from_items"] == 0


def test_soft_deleted_product_is_not_matched_by_name(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь", "S1")
    product.is_deleted = True
    db_session.commit()
    plans, stats = resolve_orders(
        db_session, [_order(items=[_line(name="Гвоздь", product_id="missing-1")])],
        {}, {"c1": customer.id})
    assert plans[0].items[0].product_id is None
    assert stats["products_from_items"] == 1


def test_line_order_follows_file(db_session):
    customer = _customer(db_session)
    first = _product(db_session, "Гвоздь", "S1")
    second = _product(db_session, "Саморез", "S2")
    plans, _ = resolve_orders(
        db_session,
        [_order(items=[_line(name="Саморез", product_id="p2"),
                       _line(name="Гвоздь", product_id="p1")])],
        {"p1": first.id, "p2": second.id}, {"c1": customer.id})
    assert [i.product_id for i in plans[0].items] == [second.id, first.id]


def test_repeated_unknown_product_lines_are_merged(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(
        db_session,
        [_order(items=[_line(name="Саморез", product_id="unknown-1", quantity=3, price=5.0),
                       _line(name="  саморез  ", product_id="unknown-2", quantity=4, price=5.0)])],
        {}, {"c1": customer.id})
    assert len(plans[0].items) == 1
    assert plans[0].items[0].quantity == 7
    assert plans[0].items[0].name == "Саморез"
    assert plans[0].items[0].product_id is None
    assert stats["items_merged"] == 1
    assert stats["products_from_items"] == 2


def test_new_product_line_without_unit_falls_back_to_default(db_session):
    customer = _customer(db_session)
    plans, _ = resolve_orders(
        db_session, [_order(items=[_line(name="Саморез", product_id="unknown-1", unit="   ")])],
        {}, {"c1": customer.id})
    assert plans[0].items[0].unit == "шт"


def test_line_with_broken_quantity_is_skipped_with_warning(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь", "S1")
    plans, stats = resolve_orders(
        db_session,
        [_order(items=[_line(product_id="p1", quantity=0),
                       _line(product_id="p1", quantity=2)])],
        {"p1": product.id}, {"c1": customer.id})
    assert [i.quantity for i in plans[0].items] == [2]
    assert stats["empty_orders"] == 0
    assert stats["items_merged"] == 0
    assert any("количеством меньше 1" in w for w in stats["warnings"])


def test_known_customer_id_used_and_unknown_one_falls_to_service(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(
        db_session, [_order(customer_id="c1"), _order(source_id="o2", customer_id="nope")],
        {}, {"c1": customer.id})
    assert [p.customer_id for p in plans] == [customer.id, None]
    assert [p.discount for p in plans] == [0.0, 0.0]
    assert stats["service_customer_planned"] == 1


@pytest.mark.parametrize("quantity", [0, -3, "abc", None, "2.5"])
def test_line_with_unusable_quantity_is_skipped_with_warning(db_session, quantity):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь", "S1")
    line = _line(product_id="p1", quantity=2)
    line["quantity"] = quantity
    plans, stats = resolve_orders(db_session, [_order(items=[line])],
                                  {"p1": product.id}, {"c1": customer.id})
    assert plans[0].items == []
    assert stats["empty_orders"] == 1
    assert any("количеством меньше 1" in w for w in stats["warnings"])


def test_line_with_unusable_price_falls_back_to_zero(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь", "S1")
    line = _line(product_id="p1")
    line["price"] = "не цена"
    plans, _ = resolve_orders(db_session, [_order(items=[line])],
                              {"p1": product.id}, {"c1": customer.id})
    assert plans[0].items[0].price == 0.0


def _query_count(db_engine, rows, product_map, customer_map):
    counted = {"n": 0}

    def count(conn, cursor, statement, parameters, context, executemany):
        counted["n"] += 1

    session = sessionmaker(bind=db_engine, autoflush=False)()
    event.listen(db_engine, "before_cursor_execute", count)
    try:
        resolve_orders(session, rows, product_map, customer_map)
    finally:
        event.remove(db_engine, "before_cursor_execute", count)
        session.close()
    return counted["n"]


def test_lookups_do_not_scale_with_batch(db_session, db_engine):
    customer = _customer(db_session)
    products = [_product(db_session, f"Товар {i}", f"S{i}") for i in range(10)]
    product_map = {f"p{i}": p.id for i, p in enumerate(products)}
    customer_map = {"c1": customer.id}
    one = _query_count(db_engine, [_order(items=[_line(name="Товар 0", product_id="p0")])],
                       product_map, customer_map)
    ten = _query_count(
        db_engine,
        [_order(source_id=f"o{i}", items=[_line(name=f"Товар {i % 10}", product_id=f"p{i % 10}")])
         for i in range(10)],
        product_map, customer_map)
    assert one == ten

