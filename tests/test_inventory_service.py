import json

import pytest

from app.modules.audit.models import AuditLog
from app.modules.inventory.models import Category, PriceHistory, Product
from app.modules.inventory.service import (
    category_contents,
    change_bulk_category,
    create_category,
    create_product,
    delete_category,
    delete_product,
    export_products_csv,
    get_category_tree,
    get_unit_by_name,
    list_categories,
    list_products,
    restore_product,
    update_category,
    update_product,
)


def _unit_id(db_session, name="шт"):
    return get_unit_by_name(db_session, name).id


def _category(db_session, name, parent_id=None):
    return create_category(db_session, name, parent_id)


def _product(db_session, category_id, name="Товар"):
    return create_product(db_session, name, f"SKU-{name}", category_id, _unit_id(db_session), 10.0, {}, 0)


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
    p = create_product(db_session, "Уголок 40", "S-40", None, _unit_id(db_session), 12.5, {}, 0, user=None)
    assert p.sku == "S-40"
    assert p.price == 12.5


def test_update_price_writes_history(db_session):
    p = create_product(db_session, "Уголок", "SKU1", None, _unit_id(db_session), 10.0, {}, 0)
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
    create_product(db_session, "Уголок", "S-40", None, _unit_id(db_session), 10.0, {}, 0)
    try:
        create_product(db_session, "Гвоздь", "  S-40  ", None, _unit_id(db_session), 5.0, {}, 0)
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
    create_product(db_session, "Уголок", "S-1", child.id, _unit_id(db_session), 10.0, {}, 0)
    try:
        delete_category(db_session, child.id)
        assert False
    except ValueError:
        pass


def test_a_child_category_blocks_deletion_and_the_message_says_so(db_session):
    root = _category(db_session, "Корень")
    _category(db_session, "Ветка", parent_id=root.id)
    with pytest.raises(ValueError, match="подкатегори"):
        delete_category(db_session, root.id)


def test_delete_category_message_names_products_and_children(db_session):
    root = _category(db_session, "Корень")
    _category(db_session, "Ветка", parent_id=root.id)
    _product(db_session, root.id, "Гвоздь")
    _product(db_session, root.id, "Шуруп")
    with pytest.raises(ValueError) as e:
        delete_category(db_session, root.id)
    assert str(e.value) == (
        "нельзя удалить категорию: в ней 2 товара и 1 подкатегория. "
        "Перенесите или удалите содержимое."
    )


@pytest.mark.parametrize("count,word", [
    (1, "товар"), (2, "товара"), (5, "товаров"),
    (11, "товаров"), (12, "товаров"), (13, "товаров"), (14, "товаров"),
    (21, "товар"), (22, "товара"), (24, "товара"),
])
def test_delete_category_message_pluralises_products(db_session, count, word):
    root = _category(db_session, f"Корень {count}")
    for i in range(count):
        _product(db_session, root.id, f"Товар {count}-{i}")
    with pytest.raises(ValueError) as e:
        delete_category(db_session, root.id)
    assert f"в ней {count} {word}." in str(e.value)


@pytest.mark.parametrize("count,word", [
    (1, "подкатегория"), (2, "подкатегории"), (5, "подкатегорий"),
    (11, "подкатегорий"), (12, "подкатегорий"), (13, "подкатегорий"), (14, "подкатегорий"),
    (21, "подкатегория"), (22, "подкатегории"), (24, "подкатегории"),
])
def test_delete_category_message_pluralises_children(db_session, count, word):
    root = _category(db_session, f"Корень {count}")
    for i in range(count):
        _category(db_session, f"Ветка {count}-{i}", parent_id=root.id)
    with pytest.raises(ValueError) as e:
        delete_category(db_session, root.id)
    assert f"в ней {count} {word}." in str(e.value)


def test_a_soft_deleted_product_does_not_block_deletion(db_session):
    category = _category(db_session, "Категория")
    _product(db_session, category.id)
    delete_product(db_session, db_session.query(Product).filter_by(category_id=category.id).one().id)
    delete_category(db_session, category.id)
    db_session.expire_all()
    assert db_session.get(Category, category.id).is_deleted is True


def test_a_soft_deleted_child_does_not_block_deletion(db_session):
    root = _category(db_session, "Корень")
    child = _category(db_session, "Ветка", parent_id=root.id)
    delete_category(db_session, child.id)
    delete_category(db_session, root.id)
    db_session.expire_all()
    assert db_session.get(Category, root.id).is_deleted is True


def test_category_contents_counts_only_live_rows(db_session):
    root = _category(db_session, "Корень")
    _category(db_session, "Ветка", parent_id=root.id)
    gone = _category(db_session, "Ушедшая", parent_id=root.id)
    delete_category(db_session, gone.id)
    _product(db_session, root.id, "Живой")
    _product(db_session, root.id, "Удалённый")
    delete_product(db_session, db_session.query(Product).filter_by(name="Удалённый").one().id)
    assert category_contents(db_session, root.id) == {"products": 1, "children": 1}


def test_reassigning_moves_products_and_children(db_session):
    root = _category(db_session, "Корень")
    child = _category(db_session, "Ветка", parent_id=root.id)
    target = _category(db_session, "Целевая")
    product = _product(db_session, root.id, "Гвоздь")

    delete_category(db_session, root.id, resolve=True, reassign_to=target.id)

    db_session.expire_all()
    assert db_session.get(Product, product.id).category_id == target.id
    assert db_session.get(Category, child.id).parent_id == target.id
    assert db_session.get(Category, root.id).is_deleted is True
    entry = db_session.query(AuditLog).filter_by(
        action="category.reassign", entity_id=root.id
    ).one()
    assert json.loads(entry.new_value) == {"to": target.id, "products": 1, "children": 1}


def test_resolving_clears_the_category_from_products_and_children(db_session):
    root = _category(db_session, "Корень")
    child = _category(db_session, "Ветка", parent_id=root.id)
    product = _product(db_session, root.id, "Гвоздь")

    delete_category(db_session, root.id, resolve=True)

    db_session.expire_all()
    assert db_session.get(Product, product.id).category_id is None
    assert db_session.get(Category, child.id).parent_id is None
    assert db_session.get(Category, root.id).is_deleted is True
    assert db_session.query(AuditLog).filter_by(
        action="category.reassign", entity_id=root.id
    ).count() == 0


def test_reassign_to_alone_does_not_release_the_block(db_session):
    root = _category(db_session, "Корень")
    target = _category(db_session, "Целевая")
    product = _product(db_session, root.id, "Гвоздь")
    with pytest.raises(ValueError, match="нельзя удалить категорию"):
        delete_category(db_session, root.id, reassign_to=target.id)
    db_session.expire_all()
    assert db_session.get(Category, root.id).is_deleted is False
    assert db_session.get(Product, product.id).category_id == root.id


def test_cannot_reassign_into_itself_or_a_descendant(db_session):
    root = _category(db_session, "Корень")
    child = _category(db_session, "Ветка", parent_id=root.id)
    product = _product(db_session, root.id, "Гвоздь")
    for target in (root.id, child.id):
        with pytest.raises(ValueError, match="себя|потомка"):
            delete_category(db_session, root.id, resolve=True, reassign_to=target)
    db_session.expire_all()
    assert db_session.get(Category, root.id).is_deleted is False
    assert db_session.get(Product, product.id).category_id == root.id
    assert db_session.get(Category, child.id).parent_id == root.id


def test_reassign_to_an_unknown_or_deleted_category_is_refused(db_session):
    root = _category(db_session, "Корень")
    product = _product(db_session, root.id, "Гвоздь")
    gone = _category(db_session, "Ушедшая")
    delete_category(db_session, gone.id)
    for target in (9999, gone.id):
        with pytest.raises(ValueError, match="не найдена"):
            delete_category(db_session, root.id, resolve=True, reassign_to=target)
    db_session.expire_all()
    assert db_session.get(Category, root.id).is_deleted is False
    assert db_session.get(Product, product.id).category_id == root.id


def test_delete_and_restore_product(db_session):
    p = create_product(db_session, "Гвоздь", "G-1", None, _unit_id(db_session, "кг"), 5.0, {}, 0)
    delete_product(db_session, p.id)
    db_session.refresh(p)
    assert p.is_deleted
    restore_product(db_session, p.id)
    db_session.refresh(p)
    assert not p.is_deleted


def test_list_products_search_and_pagination(db_session):
    create_product(db_session, "Уголок 40", "S-40", None, _unit_id(db_session), 12.5, {}, 0)
    create_product(db_session, "Гвоздь", "G-1", None, _unit_id(db_session, "кг"), 5.0, {}, 0)
    create_product(db_session, "Уголок 25", "S-25", None, _unit_id(db_session), 8.0, {}, 0)
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
    p1 = create_product(db_session, "А", "A-1", cat1.id, _unit_id(db_session), 1.0, {}, 0)
    p2 = create_product(db_session, "Б", "B-1", cat1.id, _unit_id(db_session), 2.0, {}, 0)
    p3 = create_product(db_session, "В", "C-1", cat1.id, _unit_id(db_session), 3.0, {}, 0)
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
    create_product(db_session, "Уголок 40", "S-40", child.id, _unit_id(db_session), 12.5, {}, 0)
    create_product(db_session, "Гвоздь", "G-1", None, _unit_id(db_session, "кг"), 5.0, {}, 0)
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
    p = create_product(db_session, "Гвозди", "NAIL-9", None, _unit_id(db_session), 10.0, None, 5, min_stock=7)
    assert p.min_stock == 7


def test_create_product_min_stock_defaults_to_none(db_session):
    p = create_product(db_session, "Шуруп", "SCR-9", None, _unit_id(db_session), 10.0, None, 0)
    assert p.min_stock is None


def test_update_product_min_stock(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-10", None, _unit_id(db_session), 10.0, None, 5)
    updated = update_product(db_session, p.id, {"min_stock": 12})
    assert updated.min_stock == 12


def test_update_product_min_stock_negative_rejected(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-11", None, _unit_id(db_session), 10.0, None, 5)
    with pytest.raises(ValueError):
        update_product(db_session, p.id, {"min_stock": -1})
    db_session.expire_all()
    assert db_session.get(Product, p.id).min_stock is None


def test_update_product_min_stock_negative_rejected_before_mutations(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-14", None, _unit_id(db_session), 10.0, None, 5)
    with pytest.raises(ValueError):
        update_product(db_session, p.id, {
            "sku": "NAIL-14-UPDATED", "name": "Изменённый", "price": 20.0, "min_stock": -1,
        })
    db_session.commit()
    db_session.expire_all()
    stored = db_session.get(Product, p.id)
    assert stored.min_stock is None
    assert stored.sku == "NAIL-14"
    assert stored.name == "Гвозди"
    assert stored.price == 10.0


def test_create_product_negative_min_stock_rejected(db_session):
    with pytest.raises(ValueError):
        create_product(db_session, "Гвозди", "NAIL-13", None, _unit_id(db_session), 10.0, None, 5, min_stock=-1)
    assert db_session.query(Product).filter_by(sku="NAIL-13").first() is None


def test_update_product_min_stock_in_audit(db_session):
    p = create_product(db_session, "Гвозди", "NAIL-12", None, _unit_id(db_session), 10.0, None, 5)
    update_product(db_session, p.id, {"min_stock": 3})
    entries = db_session.query(AuditLog).filter_by(action="product.update", entity_id=p.id).all()
    assert len(entries) == 1
    assert json.loads(entries[0].new_value)["min_stock"] == 3
