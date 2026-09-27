import json
import os
import sys
from datetime import date

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.modules.audit.models import AuditLog
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
from app.modules.importer.service import import_path, plan_import, run_import, save_import_path
from app.modules.inventory.models import Product
from app.modules.orders.models import Order
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


def test_rejects_order_line_that_is_not_an_object(tmp_path):
    name = _write(tmp_path, "lines.json",
                  {"orders": [{"id": "o1", "date": "2017-12-02 21:22:39",
                               "items": ["Гвоздь 2 шт"]}]})
    with pytest.raises(ImportFileError) as err:
        read_payloads(tmp_path, [name])
    assert "позиции заказа" in str(err.value)


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


def test_repeated_product_source_id_is_skipped(db_session, tmp_path):
    row = {"id": "p1", "name": "Гвоздь", "unit": "шт"}
    plans, stats = resolve_products(db_session, [row, dict(row)])
    assert [p.source_id for p in plans] == ["p1"]
    assert stats["created"] == 1
    assert stats["skipped"] == 1


def test_repeated_customer_source_id_is_skipped(db_session):
    row = {"id": "c1", "name": "ООО Ромашка"}
    plans, stats = resolve_customers(db_session, [row, dict(row)])
    assert [p.source_id for p in plans] == ["c1"]
    assert stats["created"] == 1
    assert stats["skipped"] == 1


def test_duplicate_product_id_does_not_roll_the_batch_back(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"},
                  {"id": "p1", "name": "Саморез", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(items=[_line()])],
    )
    report = run_import(db_session, tmp_path, ["export.json"])
    assert report["products"]["created"] == 1
    assert db_session.query(Product).one().name == "Гвоздь"
    assert db_session.query(Order).count() == 1
    assert db_session.query(ImportMap).count() == 3


def test_duplicate_customer_id_does_not_roll_the_batch_back(db_session, tmp_path):
    _export(
        tmp_path,
        customers=[{"id": "c1", "name": "ООО Ромашка"},
                   {"id": "c1", "name": "ООО Одуванчик"}],
        orders=[_order()],
    )
    report = run_import(db_session, tmp_path, ["export.json"])
    assert report["customers"]["created"] == 1
    assert db_session.query(Customer).one().name == "ООО Ромашка"
    assert db_session.query(Order).count() == 1


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


def test_parse_keeps_1c_wall_clock_in_the_host_timezone():
    parsed = parse_order_date("2017-12-02 21:22:39")
    local = parsed.astimezone()
    assert (local.year, local.month, local.day) == (2017, 12, 2)
    assert (local.hour, local.minute, local.second) == (21, 22, 39)


def test_parse_keeps_the_day_bucket_of_late_evening_orders():
    parsed = parse_order_date("2017-12-02 23:30:00")
    assert parsed.astimezone().date() == date(2017, 12, 2)


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


def test_skipped_order_does_not_count_a_product_it_never_creates(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(
        db_session,
        [_order(items=[_line(name="Саморез", product_id="unknown-1"),
                       _line(name="Гвоздь", product_id="p1")])],
        {"p1": 999}, {"c1": customer.id})
    assert plans == []
    assert stats["skipped"] == 1
    assert stats["products_from_items"] == 0


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
    assert stats["products_from_items"] == 1


def test_merged_lines_at_different_prices_keep_order_total(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь", "S1")
    first, second = _line(product_id="p1", quantity=2, price=10.0), _line(
        product_id="p1", quantity=3, price=20.0)
    plans, stats = resolve_orders(db_session, [_order(items=[first, second])],
                                  {"p1": product.id}, {"c1": customer.id})
    item = plans[0].items[0]
    assert item.quantity == 5
    assert item.price == pytest.approx(16.0)
    assert item.quantity * item.price == pytest.approx(
        first["quantity"] * first["price"] + second["quantity"] * second["price"])
    assert stats["items_merged"] == 1


def test_merged_new_product_lines_at_different_prices_keep_order_total(db_session):
    customer = _customer(db_session)
    first = _line(name="Саморез", product_id="unknown-1", quantity=2, price=10.0)
    second = _line(name="Саморез", product_id="unknown-2", quantity=3, price=20.0)
    plans, stats = resolve_orders(db_session, [_order(items=[first, second])],
                                  {}, {"c1": customer.id})
    item = plans[0].items[0]
    assert item.quantity == 5
    assert item.price == pytest.approx(16.0)
    assert item.quantity * item.price == pytest.approx(
        first["quantity"] * first["price"] + second["quantity"] * second["price"])
    assert stats["products_from_items"] == 1


def test_three_merged_lines_keep_order_total(db_session):
    customer = _customer(db_session)
    product = _product(db_session, "Гвоздь", "S1")
    lines = [_line(product_id="p1", quantity=q, price=p) for q, p in
             ((1, 100.0), (2, 30.5), (3, 7.25))]
    plans, _ = resolve_orders(db_session, [_order(items=lines)],
                              {"p1": product.id}, {"c1": customer.id})
    item = plans[0].items[0]
    assert item.quantity == 6
    assert item.quantity * item.price == pytest.approx(
        sum(line["quantity"] * line["price"] for line in lines))


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


def test_line_of_product_planned_for_creation_is_not_created_again(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(
        db_session, [_order(items=[_line(product_id="p1", name="Гвоздь")])],
        {}, {"c1": customer.id}, planned_products={"p1": ("Гвоздь 50мм", "шт")})
    item = plans[0].items[0]
    assert item.product_id is None
    assert item.product_source_id == "p1"
    assert item.product_name == "Гвоздь 50мм"
    assert stats["products_from_items"] == 0


def test_two_lines_of_one_planned_product_are_merged(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(
        db_session,
        [_order(items=[_line(product_id="p1", name="Гвоздь", quantity=2, price=10.0),
                       _line(product_id="p1", name="Гвоздь", quantity=3, price=20.0)])],
        {}, {"c1": customer.id}, planned_products={"p1": ("Гвоздь 50мм", "шт")})
    assert len(plans[0].items) == 1
    item = plans[0].items[0]
    assert item.product_source_id == "p1"
    assert item.quantity == 5
    assert item.quantity * item.price == pytest.approx(80.0)
    assert stats["items_merged"] == 1
    assert stats["products_from_items"] == 0


def test_planned_product_line_takes_unit_from_the_planned_product(db_session):
    customer = _customer(db_session)
    plans, _ = resolve_orders(
        db_session, [_order(items=[_line(product_id="p1", name="Гвоздь", unit="шт")])],
        {}, {"c1": customer.id}, planned_products={"p1": ("Гвоздь 50мм", "короб")})
    item = plans[0].items[0]
    assert item.product_unit == "короб"
    assert item.product_name == "Гвоздь 50мм"
    assert item.unit == "шт"


def test_planned_product_wins_over_product_with_the_same_name_in_base(db_session):
    customer = _customer(db_session)
    _product(db_session, "Гвоздь 50мм", "S1")
    plans, stats = resolve_orders(
        db_session, [_order(items=[_line(product_id="p1", name="Гвоздь")])],
        {}, {"c1": customer.id}, planned_products={"p1": ("Гвоздь 50мм", "шт")})
    assert plans[0].items[0].product_id is None
    assert plans[0].items[0].product_source_id == "p1"
    assert stats["products_from_items"] == 0


def test_order_of_customer_planned_for_creation_is_not_sent_to_service(db_session):
    plans, stats = resolve_orders(db_session, [_order(customer_id="c1")], {}, {},
                                  planned_customers={"c1"})
    assert plans[0].customer_id is None
    assert plans[0].customer_source_id == "c1"
    assert plans[0].discount == 0.0
    assert stats["service_customer_planned"] == 0


def test_order_of_existing_customer_ignores_planned_customers(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(db_session, [_order(customer_id="c1")], {},
                                  {"c1": customer.id}, planned_customers={"c1"})
    assert plans[0].customer_id == customer.id
    assert plans[0].customer_source_id is None
    assert stats["service_customer_planned"] == 0


def test_order_already_in_import_map_is_matched_not_recreated(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(db_session, [_order()], {}, {"c1": customer.id},
                                  imported_orders={"o1"})
    assert plans == []
    assert stats["created"] == 0
    assert stats["matched"] == 1
    assert stats["skipped"] == 0


def test_line_pointing_to_deleted_product_skips_order(db_session):
    customer = _customer(db_session)
    plans, stats = resolve_orders(db_session, [_order(items=[_line(product_id="p1")])],
                                  {"p1": 999}, {"c1": customer.id})
    assert plans == []
    assert stats["skipped"] == 1
    assert any("o1" in w and "p1" in w for w in stats["warnings"])


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
    assert (one, ten) == (4, 4)


def _payload(products=(), customers=(), orders=()):
    return {"products": list(products), "customers": list(customers), "orders": list(orders)}


def _export(tmp_path, *, products=(), customers=(), orders=(), name="export.json"):
    return _write(tmp_path, name, _payload(products, customers, orders))


def test_plan_writes_nothing(db_session, tmp_path):
    _export(tmp_path, products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}])
    report = plan_import(db_session, tmp_path, ["export.json"])
    assert report["products"]["created"] == 1
    assert db_session.query(Product).count() == 0
    assert db_session.query(ImportMap).count() == 0


def test_run_import_creates_and_maps_everything(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка", "inn": "7700000000"}],
        orders=[{"id": "o1", "number": "0000-000001", "date": "2017-12-02 21:22:39",
                 "customer_id": "c1",
                 "items": [{"product_id": "p1", "name": "Гвоздь", "unit": "шт",
                            "quantity": 2, "price": 10.0}]}],
    )
    report = run_import(db_session, tmp_path, ["export.json"])
    assert report["products"]["created"] == 1
    assert report["customers"]["created"] == 1
    assert report["orders"]["created"] == 1
    order = db_session.query(Order).one()
    item = order.items[0]
    assert item.product_id == db_session.query(Product).one().id
    assert item.product_name_snapshot == "Гвоздь"
    assert item.unit_snapshot == "шт"
    assert order.customer_id == db_session.query(Customer).one().id
    assert order.status == "done"
    assert order.total_amount == 20.0
    assert db_session.query(ImportMap).count() == 3


def test_order_from_1c_number_is_kept_in_map(db_session, tmp_path):
    _export(
        tmp_path,
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[{"id": "o1", "number": "0000-000001", "date": "2017-12-02 21:22:39",
                 "customer_id": "c1", "items": []}],
    )
    run_import(db_session, tmp_path, ["export.json"])
    row = db_session.query(ImportMap).filter(ImportMap.entity_type == "order").one()
    assert row.source_id == "o1"
    assert row.source_label == "0000-000001"
    assert row.entity_id == db_session.query(Order).one().id


def test_long_name_survives_and_map_rows_hold_no_name(db_session, tmp_path):
    long_name = "Гвоздь оцинкованный, " * 5 + "плата"
    assert len(long_name) > 64
    _export(
        tmp_path,
        products=[{"id": "p1", "name": long_name, "unit": "шт"}],
        customers=[{"id": "c1", "name": long_name}],
    )
    report = run_import(db_session, tmp_path, ["export.json"])
    assert report["products"]["created"] == 1
    assert report["customers"]["created"] == 1
    assert db_session.query(Product).one().name == long_name
    labels = {row.entity_type: row.source_label for row in db_session.query(ImportMap).all()}
    assert labels == {"product": None, "customer": None}


def test_import_does_not_change_stock(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[{"id": "o1", "number": "1", "date": "2017-12-02 21:22:39", "customer_id": "c1",
                 "items": [{"product_id": "p1", "name": "Гвоздь", "unit": "шт",
                            "quantity": 5, "price": 10.0}]}],
    )
    run_import(db_session, tmp_path, ["export.json"])
    assert db_session.query(Product).one().rest == 0


def test_item_only_order_creates_product_with_sku(db_session, tmp_path):
    _export(
        tmp_path,
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[{"id": "o1", "number": "1", "date": "2017-12-02 21:22:39", "customer_id": "c1",
                 "items": [{"product_id": "unknown-1", "name": "Саморез", "unit": "шт",
                            "quantity": 4, "price": 5.0}]}],
    )
    run_import(db_session, tmp_path, ["export.json"])
    product = db_session.query(Product).one()
    assert product.name == "Саморез"
    assert product.sku is not None
    assert product.sku.startswith("1C-")
    assert db_session.query(Order).one().items[0].product_id == product.id


def test_repeat_import_creates_nothing(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[{"id": "o1", "number": "1", "date": "2017-12-02 21:22:39", "customer_id": "c1",
                 "items": [{"product_id": "p1", "name": "Гвоздь", "unit": "шт",
                            "quantity": 1, "price": 10.0}]}],
    )
    run_import(db_session, tmp_path, ["export.json"])
    second = run_import(db_session, tmp_path, ["export.json"])
    assert second["orders"]["created"] == 0
    assert db_session.query(Order).count() == 1
    assert db_session.query(Product).count() == 1


def test_order_number_comes_from_the_app_not_from_1c(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "короб"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[{"id": "o1", "number": "0000-000001", "date": "2017-12-02 21:22:39",
                 "customer_id": "c1",
                 "items": [{"product_id": "p1", "name": "Гвоздь", "unit": "шт",
                            "quantity": 2, "price": 10.0}]}],
    )
    expected = next_order_number(db_session)
    run_import(db_session, tmp_path, ["export.json"])
    order = db_session.query(Order).one()
    label = db_session.query(ImportMap).filter(ImportMap.entity_type == "order").one()
    assert order.number == expected == "00001"
    assert order.number != label.source_label
    assert label.source_label == "0000-000001"
    assert order.items[0].unit_snapshot == "короб"


def test_order_without_customer_uses_service_one(db_session, tmp_path):
    _export(tmp_path, orders=[{"id": "o1", "number": "1",
                               "date": "2017-12-02 21:22:39",
                               "customer_id": None, "items": []}])
    report = run_import(db_session, tmp_path, ["export.json"])
    assert report["service_customer_created"] is True
    customer = db_session.query(Customer).one()
    assert customer.name == "Без контрагента (1С)"
    assert db_session.query(Order).one().customer_id == customer.id


def test_failure_rolls_everything_back(db_session, tmp_path, monkeypatch):
    import app.modules.importer.service as service
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
    )

    def boom(*args, **kwargs):
        raise RuntimeError("сбой на середине")

    monkeypatch.setattr(service, "_write_customers", boom)
    with pytest.raises(RuntimeError):
        run_import(db_session, tmp_path, ["export.json"])
    assert db_session.query(Product).count() == 0
    assert db_session.query(Customer).count() == 0
    assert db_session.query(ImportMap).count() == 0


def test_failure_after_the_audit_write_rolls_everything_back(db_session, tmp_path, monkeypatch):
    import app.modules.importer.service as service
    real_log_action = service.log_action
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(items=[_line()])],
    )

    def boom(*args, **kwargs):
        real_log_action(*args, **kwargs)
        raise RuntimeError("сбой после записи в аудит")

    monkeypatch.setattr(service, "log_action", boom)
    with pytest.raises(RuntimeError):
        run_import(db_session, tmp_path, ["export.json"])
    assert db_session.query(Product).count() == 0
    assert db_session.query(Customer).count() == 0
    assert db_session.query(Order).count() == 0
    assert db_session.query(ImportMap).count() == 0
    assert db_session.query(AuditLog).count() == 0


def test_run_import_writes_single_audit_entry(db_session, tmp_path):
    user = _admin(db_session)
    _export(tmp_path,
            products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
            customers=[{"id": "c1", "name": "ООО Ромашка"}],
            orders=[{"id": "o1", "number": "1", "date": "2017-12-02 21:22:39",
                     "customer_id": "c1", "items": []}])
    run_import(db_session, tmp_path, ["export.json"], user=user)
    entries = db_session.query(AuditLog).filter(AuditLog.action == "import.run").all()
    assert len(entries) == 1
    assert "products" in entries[0].new_value
    assert db_session.query(AuditLog).count() == 1


def test_orders_reach_their_own_1c_customers(db_session, tmp_path):
    _export(
        tmp_path,
        customers=[{"id": "c1", "name": "ООО Ромашка"}, {"id": "c2", "name": "ООО Одуванчик"}],
        orders=[_order(source_id="o1", customer_id="c1"), _order(source_id="o2", customer_id="c2")],
    )
    report = run_import(db_session, tmp_path, ["export.json"])
    names = dict(db_session.query(Customer.id, Customer.name).all())
    assert db_session.query(Customer).count() == 2
    assert [names[o.customer_id] for o in db_session.query(Order).order_by(Order.id).all()] == [
        "ООО Ромашка", "ООО Одуванчик"]
    assert report["service_customer_planned"] is False
    assert report["service_customer_created"] is False


def test_product_of_the_products_section_is_created_once(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(source_id="o1", items=[_line()]),
                _order(source_id="o2", number="2", items=[_line(quantity=3)])],
    )
    report = run_import(db_session, tmp_path, ["export.json"])
    product = db_session.query(Product).one()
    orders = db_session.query(Order).order_by(Order.id).all()
    assert orders[0].items[0].product_id == orders[1].items[0].product_id == product.id
    assert report["orders"]["products_from_items"] == 0


def test_existing_stock_is_untouched_by_a_done_order(db_session, tmp_path):
    product = _product(db_session, "Гвоздь", "S1", rest=100)
    customer = _customer(db_session)
    db_session.add_all([
        ImportMap(entity_type="product", source_id="p1", entity_id=product.id),
        ImportMap(entity_type="customer", source_id="c1", entity_id=customer.id)])
    db_session.commit()
    _export(tmp_path, orders=[_order(items=[_line(quantity=5, price=10.0)])])
    run_import(db_session, tmp_path, ["export.json"])
    order = db_session.query(Order).one()
    assert order.status == "done"
    assert order.total_amount == 50.0
    assert db_session.query(Product).one().rest == 100


def test_stale_product_map_row_skips_only_that_order(db_session, tmp_path):
    db_session.add(ImportMap(entity_type="product", source_id="p1", entity_id=999))
    db_session.commit()
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"},
                  {"id": "p2", "name": "Саморез", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(source_id="o1", items=[_line(product_id="p1", name="Гвоздь")]),
                _order(source_id="o2", number="2", items=[_line(product_id="p2", name="Саморез")])],
    )
    report = run_import(db_session, tmp_path, ["export.json"])
    assert report["orders"]["created"] == 1
    assert report["orders"]["skipped"] == 1
    order = db_session.query(Order).one()
    assert order.total_amount == 10.0
    assert order.items[0].product_name_snapshot == "Саморез"
    assert db_session.query(Product).one().name == "Саморез"
    assert any("o1" in w and "p1" in w for w in report["warnings"])


def test_product_created_from_merged_lines_keeps_price_zero(db_session, tmp_path):
    _export(
        tmp_path,
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(items=[_line(name="Саморез", product_id="unknown-1", quantity=2, price=10.0),
                              _line(name="Саморез", product_id="unknown-2", quantity=3, price=20.0)])],
    )
    run_import(db_session, tmp_path, ["export.json"])
    product = db_session.query(Product).one()
    assert product.name == "Саморез"
    assert product.price == 0.0
    assert db_session.query(Order).one().total_amount == 80.0


def test_sku_of_product_created_from_a_line_does_not_collide(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(items=[_line(name="Саморез", product_id="unknown-1")])],
    )
    run_import(db_session, tmp_path, ["export.json"])
    skus = sorted(p.sku for p in db_session.query(Product).all())
    assert skus == ["1C-00001", "1C-00002"]


def test_preview_reports_what_the_import_does(db_session, tmp_path):
    _export(
        tmp_path,
        products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(items=[_line()])],
    )
    preview = plan_import(db_session, tmp_path, ["export.json"])
    report = run_import(db_session, tmp_path, ["export.json"])
    assert report["products"] == preview["products"]
    assert report["customers"] == preview["customers"]
    assert report["orders"] == preview["orders"]
    assert report["files"] == preview["files"] == ["export.json"]
    assert db_session.query(Product).count() == preview["products"]["created"]
    assert db_session.query(Customer).count() == preview["customers"]["created"]
    assert db_session.query(Order).count() == preview["orders"]["created"]


def test_second_preview_after_import_counts_matched_orders(db_session, tmp_path):
    _export(tmp_path, customers=[{"id": "c1", "name": "ООО Ромашка"}], orders=[_order()])
    run_import(db_session, tmp_path, ["export.json"])
    second = plan_import(db_session, tmp_path, ["export.json"])
    assert second["orders"]["created"] == 0
    assert second["orders"]["matched"] == 1
    assert second["customers"]["created"] == 0


def test_import_path_defaults_and_round_trip(db_session):
    assert import_path(db_session) == ".system_folder/1c_export"
    save_import_path(db_session, "  data/1c  ")
    assert import_path(db_session) == "data/1c"


def test_blank_import_path_is_rejected(db_session):
    with pytest.raises(ValueError):
        save_import_path(db_session, "   ")


def _selects(db_engine, folder, name, run):
    counted = {"n": 0}

    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counted["n"] += 1

    session = sessionmaker(bind=db_engine, autoflush=False)()
    event.listen(db_engine, "before_cursor_execute", count)
    try:
        (run_import if run else plan_import)(session, folder, [name])
    finally:
        event.remove(db_engine, "before_cursor_execute", count)
        session.close()
    return counted["n"]


def test_preview_queries_do_not_scale_with_batch(db_engine, tmp_path):
    one = _export(tmp_path, products=[{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
                  customers=[{"id": "c1", "name": "ООО Ромашка"}],
                  orders=[_order(items=[_line()]),
                          _order(source_id="o9", number="9",
                                 items=[_line(name="Саморез", product_id="unknown-1")])],
                  name="one.json")
    ten = _export(
        tmp_path,
        products=[{"id": f"p{i}", "name": f"Товар {i}", "unit": "шт"} for i in range(10)],
        customers=[{"id": "c1", "name": "ООО Ромашка"}],
        orders=[_order(source_id=f"o{i}", number=str(i + 1),
                       items=[_line(name=f"Товар {i}", product_id=f"p{i}")])
                for i in range(10)]
        + [_order(source_id=f"x{i}", number=str(i + 1),
                  items=[_line(name=f"Шуруп {i}", product_id=f"unknown-{i}")])
           for i in range(10)],
        name="ten.json")
    assert _selects(db_engine, tmp_path, one, False) == _selects(db_engine, tmp_path, ten, False)
    assert _selects(db_engine, tmp_path, one, True) == _selects(db_engine, tmp_path, ten, True)

