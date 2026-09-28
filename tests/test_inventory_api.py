from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory.models import Product

PASSWORD = "password123"


def _ensure_user(db_session, username, role):
    u = db_session.query(User).filter(User.username == username).first()
    if u is None:
        u = User(
            username=username,
            password_hash=hash_password(PASSWORD),
            role_id=get_role_by_name(db_session, role).id,
        )
        db_session.add(u)
        db_session.commit()
    return u


def _user(client, db_session, username, role):
    u = _ensure_user(db_session, username, role)
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)
    return u


def _admin(client, db_session):
    return _user(client, db_session, "boss", "admin")


def _seller(client, db_session):
    return _user(client, db_session, "seller1", "seller")


CATEGORY = {"name": "Пищевые", "parent_id": None}
PRODUCT = {
    "name": "Молоко",
    "sku": "MILK-1",
    "category_id": None,
    "price": 80.0,
    "rest": 10,
}


def _make_category(client, admin=None, name="Продукты", parent_id=None):
    r = client.post("/api/categories", json={"name": name, "parent_id": parent_id})
    assert r.status_code == 201
    return r.json()


def _make_product(client, **overrides):
    payload = dict(PRODUCT)
    payload.update(overrides)
    r = client.post("/api/products", json=payload)
    assert r.status_code == 201
    return r.json()


def test_admin_creates_category_and_product(client, db_session):
    _admin(client, db_session)
    cat = _make_category(client, admin=True)
    product = _make_product(client, category_id=cat["id"])
    assert product["name"] == "Молоко"
    assert product["category"]["id"] == cat["id"]


def test_seller_cannot_create_product(client, db_session):
    _seller(client, db_session)
    r = client.post("/api/products", json=PRODUCT)
    assert r.status_code == 403


def test_seller_cannot_create_category(client, db_session):
    _seller(client, db_session)
    r = client.post("/api/categories", json=CATEGORY)
    assert r.status_code == 403


def test_post_rest_requires_stock_change(client, db_session):
    _admin(client, db_session)
    role = Role(name="editor", is_system=False, is_super=False,
                permissions=["products.view", "products.edit"])
    db_session.add(role)
    db_session.commit()
    _user(client, db_session, "editor1", "editor")
    r = client.post("/api/products", json={"name": "Без остатка", "sku": "NOSTOCK-1"})
    assert r.status_code == 201
    r = client.post("/api/products", json={"name": "С остатком", "sku": "STOCK-1", "rest": 5})
    assert r.status_code == 403


def test_post_price_requires_price_change(client, db_session):
    _admin(client, db_session)
    role = Role(name="creator", is_system=False, is_super=False,
                permissions=["products.view", "products.edit"])
    db_session.add(role)
    db_session.commit()
    _user(client, db_session, "creator1", "creator")
    r = client.post("/api/products", json={"name": "Без цены", "sku": "NOPRICE-1"})
    assert r.status_code == 201
    r = client.post("/api/products", json={"name": "С ценой", "sku": "PRICE-1", "price": 100.0})
    assert r.status_code == 403


def test_seller_patch_rest_forbidden_warehouseman_allowed(client, db_session):
    _admin(client, db_session)
    p = _make_product(client)
    _seller(client, db_session)
    r = client.patch(f"/api/products/{p['id']}", json={"rest": 3})
    assert r.status_code == 403
    _user(client, db_session, "wh1", "warehouseman")
    r = client.patch(f"/api/products/{p['id']}", json={"rest": 3})
    assert r.status_code == 200
    assert r.json()["rest"] == 3


def test_seller_patch_price_forbidden_manager_allowed(client, db_session):
    _admin(client, db_session)
    p = _make_product(client)
    _seller(client, db_session)
    r = client.patch(f"/api/products/{p['id']}", json={"price": 90.0})
    assert r.status_code == 403
    mgr = _user(client, db_session, "mgr1", "manager")
    r = client.patch(f"/api/products/{p['id']}", json={"price": 90.0})
    assert r.status_code == 200
    assert r.json()["price"] == 90.0
    assert mgr.id is not None


def test_patch_rest_and_price_requires_both(client, db_session):
    _admin(client, db_session)
    p = _make_product(client)
    role = Role(name="editor2", is_system=False, is_super=False,
                permissions=["products.view", "products.edit"])
    db_session.add(role)
    db_session.commit()
    _user(client, db_session, "editor2", "editor2")
    r = client.patch(f"/api/products/{p['id']}", json={"name": "Переименован"})
    assert r.status_code == 200
    r = client.patch(f"/api/products/{p['id']}", json={"rest": 1, "price": 100.0})
    assert r.status_code == 403


def test_patch_name_and_price_requires_both_permissions(client, db_session):
    _admin(client, db_session)
    p = _make_product(client)
    role = Role(name="pricer", is_system=False, is_super=False,
                permissions=["products.view", "price.change"])
    db_session.add(role)
    db_session.commit()
    _user(client, db_session, "pricer1", "pricer")
    r = client.patch(f"/api/products/{p['id']}", json={"name": "Переименован", "price": 90.0})
    assert r.status_code == 403
    role = Role(name="pricer2", is_system=False, is_super=False,
                permissions=["products.view", "products.edit", "price.change"])
    db_session.add(role)
    db_session.commit()
    _user(client, db_session, "pricer2", "pricer2")
    r = client.patch(f"/api/products/{p['id']}", json={"name": "Переименован", "price": 90.0})
    assert r.status_code == 200
    assert r.json()["name"] == "Переименован"
    assert r.json()["price"] == 90.0


def test_category_name_reusable_after_delete(client, db_session):
    _admin(client, db_session)
    cat = _make_category(client, name="Кровля")
    r = client.delete(f"/api/categories/{cat['id']}")
    assert r.status_code == 204
    r = client.post("/api/categories", json={"name": "Кровля"})
    assert r.status_code == 201


def test_update_category_name_conflicts_with_live_sibling(client, db_session):
    _admin(client, db_session)
    _make_category(client, name="Кровля")
    b = _make_category(client, name="Изоляция")
    r = client.patch(f"/api/categories/{b['id']}", json={"name": "Кровля"})
    assert r.status_code == 409


def test_update_category_to_deleted_sibling_name_ok(client, db_session):
    _admin(client, db_session)
    a = _make_category(client, name="Кровля")
    b = _make_category(client, name="Изоляция")
    r = client.delete(f"/api/categories/{a['id']}")
    assert r.status_code == 204
    r = client.patch(f"/api/categories/{b['id']}", json={"name": "Кровля"})
    assert r.status_code == 200
    assert r.json()["name"] == "Кровля"


def test_lists_visible_with_products_view(client, db_session):
    _admin(client, db_session)
    cat = _make_category(client, admin=None)
    _make_product(client, category_id=cat["id"])
    _seller(client, db_session)
    r = client.get("/api/products")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    r = client.get("/api/categories")
    assert r.status_code == 200
    assert any(c["name"] == cat["name"] for c in r.json())


def test_search_products(client, db_session):
    _admin(client, db_session)
    _make_product(client, name="Сыр Российский", sku="CHEESE-1")
    _seller(client, db_session)
    r = client.get("/api/products", params={"search": "сыр"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r = client.get("/api/products", params={"search": "Сыр"})
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_bulk_category_requires_products_edit(client, db_session):
    _admin(client, db_session)
    cat = _make_category(client, admin=None)
    p = _make_product(client)
    dest = _make_category(client, admin=None, name="Напитки")
    _seller(client, db_session)
    payload = {"product_ids": [p["id"]], "category_id": dest["id"]}
    r = client.post("/api/products/bulk-category", json=payload)
    assert r.status_code == 403
    _admin(client, db_session)
    r = client.post("/api/products/bulk-category", json=payload)
    assert r.status_code == 200
    assert r.json()["updated"] == 1
    assert cat["id"] is not None


def test_export_csv_permissions(client, db_session):
    _admin(client, db_session)
    _make_product(client)
    r = client.get("/api/products/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/csv")
    assert "Молоко;" in r.text
    _seller(client, db_session)
    r = client.get("/api/products/export.csv")
    assert r.status_code == 403


def test_delete_and_restore_require_products_edit(client, db_session):
    _admin(client, db_session)
    p = _make_product(client)
    _seller(client, db_session)
    r = client.delete(f"/api/products/{p['id']}")
    assert r.status_code == 403
    r = client.post(f"/api/products/restore/{p['id']}")
    assert r.status_code == 403
    _admin(client, db_session)
    r = client.delete(f"/api/products/{p['id']}")
    assert r.status_code == 204
    r = client.post(f"/api/products/restore/{p['id']}")
    assert r.status_code == 200
    assert r.json()["is_deleted"] is False


def test_product_detail_includes_category_and_price_history(client, db_session):
    admin = _admin(client, db_session)
    cat = _make_category(client, admin=None)
    sub = _make_category(client, admin=None, name="Молочка", parent_id=cat["id"])
    p = _make_product(client, category_id=sub["id"])
    client.patch(f"/api/products/{p['id']}", json={"price": 95.0})
    r = client.get(f"/api/products/{p['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["category"]["id"] == sub["id"]
    assert body["category"]["parent_name"] == cat["name"]
    assert len(body["price_history"]) == 1
    assert body["price_history"][0]["old_price"] == 80.0
    assert body["price_history"][0]["new_price"] == 95.0
    assert admin.id is not None


def test_create_product_with_min_stock(client, db_session):
    _admin(client, db_session)
    r = client.post("/api/products", json={
        "name": "Гвозди", "sku": "NAIL-A1", "price": 10.0, "min_stock": 9,
    })
    assert r.status_code == 201
    assert r.json()["min_stock"] == 9


def test_patch_product_min_stock(client, db_session):
    _admin(client, db_session)
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-A2"}).json()["id"]
    r = client.patch(f"/api/products/{pid}", json={"min_stock": 15})
    assert r.status_code == 200
    assert r.json()["min_stock"] == 15


def test_create_product_negative_min_stock_returns_400(client, db_session):
    _admin(client, db_session)
    r = client.post("/api/products", json={
        "name": "Гвозди", "sku": "NAIL-A4", "min_stock": -5,
    })
    assert r.status_code == 400
    assert db_session.query(Product).filter_by(sku="NAIL-A4").first() is None


def test_patch_product_negative_min_stock_returns_400(client, db_session):
    _admin(client, db_session)
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-A3"}).json()["id"]
    r = client.patch(f"/api/products/{pid}", json={"min_stock": -5})
    assert r.status_code == 400


def test_patch_product_null_unit_falls_back_to_pieces(client, db_session):
    from app.modules.inventory.models import Unit
    _admin(client, db_session)
    pieces = db_session.query(Unit).filter(Unit.name == "шт").one()
    kg = db_session.query(Unit).filter(Unit.name == "кг").one()
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-U1",
                                             "unit_id": kg.id}).json()["id"]
    assert client.get(f"/api/products/{pid}").json()["unit"] == "кг"
    r = client.patch(f"/api/products/{pid}", json={"unit_id": None})
    assert r.status_code == 200
    assert r.json()["unit"] == "шт"
    assert r.json()["unit_id"] == pieces.id
    stored = db_session.get(Product, pid)
    assert stored.unit_id == pieces.id


def test_patch_product_keeps_a_chosen_unit(client, db_session):
    from app.modules.inventory.models import Unit
    _admin(client, db_session)
    kg = db_session.query(Unit).filter(Unit.name == "кг").one()
    l = db_session.query(Unit).filter(Unit.name == "л").one()
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-U2"}).json()["id"]
    r = client.patch(f"/api/products/{pid}", json={"unit_id": l.id})
    assert r.status_code == 200
    assert r.json()["unit"] == "л"
    assert db_session.get(Product, pid).unit_id == l.id
    assert kg.id != l.id


def test_patch_product_rejects_unknown_unit_id(client, db_session):
    _admin(client, db_session)
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-U3"}).json()["id"]
    r = client.patch(f"/api/products/{pid}", json={"unit_id": 9999})
    assert r.status_code == 409
    assert db_session.get(Product, pid).unit_id is not None
