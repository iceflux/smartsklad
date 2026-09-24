import pytest

from app.modules.audit.models import AuditLog
from app.modules.inventory.models import PriceHistory, Product
from app.modules.inventory.service import (
    change_bulk_category,
    create_category,
    create_product,
    delete_category,
    delete_product,
    export_products_csv,
    get_category_tree,
    list_categories,
    list_products,
    restore_product,
    update_category,
    update_product,
)


def test_create_category_creates_tree(db_session):
    root = create_category(db_session, "Кровля")
    child = create_category(db_session, "Черепица", root.id)
    assert child.parent_id == root.id


def test_duplicate_category_rejected(db_session):
    create_category(db_session, "Кровля")
    try:
        create_category(db_session, "Кровля")
        assert False
    except ValueError:
        pass


def test_create_product_sets_price_history(db_session):
    p = create_product(db_session, "Уголок 40", "S-40", None, "шт", 12.5, {}, 0, user=None)
    assert p.sku == "S-40"
    assert p.price == 12.5


def test_update_price_writes_history(db_session):
    p = create_product(db_session, "Уголок", "SKU1", None, "шт", 10.0, {}, 0)
    update_product(db_session, p.id, {"price": 15.0}, user=None)
    db_session.refresh(p)
    h = db_session.query(PriceHistory).filter_by(product_id=p.id).all()
    assert len(h) == 1 and h[0].old_price == 10.0 and h[0].new_price == 15.0
    entry = (
        db_session.query(AuditLog)
        .filter_by(action="product.update", entity_id=p.id)
        .first()
    )
    assert entry is not None
    assert '"price": 10.0' in entry.old_value and '"price": 15.0' in entry.new_value


def test_duplicate_sku_rejected(db_session):
    create_product(db_session, "Уголок", "S-40", None, "шт", 10.0, {}, 0)
    try:
        create_product(db_session, "Гвоздь", "  S-40  ", None, "шт", 5.0, {}, 0)
        assert False
    except ValueError:
        pass


def test_update_category_renames_and_audits(db_session):
    root = create_category(db_session, "Кровля")
    child = create_category(db_session, "Черепица", root.id)
    updated = update_category(db_session, child.id, "Гибкая черепица", root.id)
    assert updated.name == "Гибкая черепица"
    entry = (
        db_session.query(AuditLog)
        .filter_by(action="category.update", entity_id=child.id)
        .first()
    )
    assert entry is not None
    assert '"name"' in entry.old_value and '"Гибкая черепица"' in entry.new_value


def test_update_category_rejects_self_parent_and_dup_sibling(db_session):
    parent = create_category(db_session, "Кровля")
    child = create_category(db_session, "Черепица", parent.id)
    sibling = create_category(db_session, "Ондулин", parent.id)
    try:
        update_category(db_session, child.id, "Черепица", child.id)
        assert False
    except ValueError:
        pass
    try:
        update_category(db_session, sibling.id, "Черепица", parent.id)
        assert False
    except ValueError:
        pass


def test_delete_category_blocked_with_children_or_products(db_session):
    root = create_category(db_session, "Кровля")
    child = create_category(db_session, "Черепица", root.id)
    try:
        delete_category(db_session, root.id)
        assert False
    except ValueError:
        pass
    create_product(db_session, "Уголок", "S-1", child.id, "шт", 10.0, {}, 0)
    try:
        delete_category(db_session, child.id)
        assert False
    except ValueError:
        pass


def test_delete_and_restore_product(db_session):
    p = create_product(db_session, "Гвоздь", "G-1", None, "шт", 5.0, {}, 0)
    delete_product(db_session, p.id)
    db_session.refresh(p)
    assert p.is_deleted
    restore_product(db_session, p.id)
    db_session.refresh(p)
    assert not p.is_deleted


def test_list_products_search_and_pagination(db_session):
    create_product(db_session, "Уголок 40", "S-40", None, "шт", 12.5, {}, 0)
    create_product(db_session, "Гвоздь", "G-1", None, "кг", 5.0, {}, 0)
    create_product(db_session, "Уголок 25", "S-25", None, "шт", 8.0, {}, 0)
    found, total = list_products(db_session, search="Уголок")
    assert total == 2 and all("Уголок" in p.name for p in found)
    page, page_total = list_products(db_session, page=1, page_size=2)
    assert page_total == 3 and len(page) == 2
    delete_product(db_session, page[0].id)
    live, live_total = list_products(db_session)
    assert live_total == 2 and not any(p.is_deleted for p in live)


def test_change_bulk_category(db_session):
    cat1 = create_category(db_session, "Категория 1")
    cat2 = create_category(db_session, "Категория 2")
    p1 = create_product(db_session, "А", "A-1", cat1.id, "шт", 1.0, {}, 0)
    p2 = create_product(db_session, "Б", "B-1", cat1.id, "шт", 2.0, {}, 0)
    p3 = create_product(db_session, "В", "C-1", cat1.id, "шт", 3.0, {}, 0)
    delete_product(db_session, p3.id)
    count = change_bulk_category(db_session, [p1.id, p2.id, p3.id, 9999], cat2.id)
    assert count == 2
    db_session.refresh(p1)
    db_session.refresh(p2)
    db_session.refresh(p3)
    assert p1.category_id == cat2.id and p2.category_id == cat2.id
    assert p3.category_id == cat1.id
    try:
        change_bulk_category(db_session, [p1.id], 9999)
        assert False
    except ValueError:
        pass


def test_export_products_csv(db_session):
    root = create_category(db_session, "Кровля")
    child = create_category(db_session, "Черепица", root.id)
    create_product(db_session, "Уголок 40", "S-40", child.id, "шт", 12.5, {}, 0)
    create_product(db_session, "Гвоздь", "G-1", None, "кг", 5.0, {}, 0)
    text = export_products_csv(db_session).decode("utf-8")
    assert text.startswith("\ufeff")
    lines = text.replace("\ufeff", "", 1).splitlines()
    assert lines[0] == "name;sku;category;unit;price;rest"
    assert "Кровля / Черепица" in text
    assert "S-40;Кровля / Черепица;шт;12.5;0" in text
    assert "G-1;;кг;5.0;0" in text


def test_get_category_tree(db_session):
    root = create_category(db_session, "Б")
    child = create_category(db_session, "А", root.id)
    create_category(db_session, "Внук", child.id)
    tree = get_category_tree(db_session)
    assert tree == [
        {
            "id": root.id,
            "name": "Б",
            "parent_id": None,
            "children": [{"id": child.id, "name": "А"}],
        }
    ]


def test_list_categories_excludes_deleted(db_session):
    a = create_category(db_session, "А")
    create_category(db_session, "Б")
    delete_category(db_session, a.id)
    names = [c.name for c in list_categories(db_session)]
    assert names == ["Б"]


def test_create_product_with_min_stock(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-9", None, "шт", 10.0, None, 5, min_stock=7)
    assert p.min_stock == 7


def test_create_product_min_stock_defaults_to_none(db_session):
    p = create_product(db_session, "Шуруп", "SCR-9", None, "шт", 10.0, None, 0)
    assert p.min_stock is None


def test_update_product_min_stock(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-10", None, "шт", 10.0, None, 5)
    updated = update_product(db_session, p.id, {"min_stock": 12})
    assert updated.min_stock == 12


def test_update_product_min_stock_negative_rejected(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-11", None, "шт", 10.0, None, 5)
    with pytest.raises(ValueError):
        update_product(db_session, p.id, {"min_stock": -1})
    db_session.expire_all()
    assert db_session.get(Product, p.id).min_stock is None


def test_update_product_min_stock_in_audit(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-12", None, "шт", 10.0, None, 5)
    update_product(db_session, p.id, {"min_stock": 3})
    entries = db_session.query(AuditLog).filter_by(action="product.update", entity_id=p.id).all()
    assert len(entries) == 1
    assert '"min_stock": 3' in entries[0].new_value
