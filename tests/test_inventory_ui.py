from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login

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


def _login(client, db_session, username, role):
    _ensure_user(db_session, username, role)
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


def _make_product(client, **overrides):
    payload = {
        "name": "Молоко",
        "sku": "MILK-1",
        "category_id": None,
        "price": 80.0,
        "rest": 10,
    }
    payload.update(overrides)
    r = client.post("/api/products", json=payload)
    assert r.status_code == 201
    return r.json()


def test_products_page_redirects_anonymous(client):
    r = client.get("/products", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_products_page_forbidden_without_products_view(client, db_session):
    role = Role(name="noview", is_system=False, is_super=False, permissions=["orders.view"])
    db_session.add(role)
    db_session.commit()
    _login(client, db_session, "noview1", "noview")
    r = client.get("/products")
    assert r.status_code == 403


def test_products_and_categories_pages_render_for_admin(client, db_session):
    _admin(client, db_session)
    _make_product(client)
    r = client.get("/products")
    assert r.status_code == 200
    assert "Молоко" in r.text
    assert "Товары" in r.text
    r = client.get("/categories")
    assert r.status_code == 200


def test_all_inventory_pages_render(client, db_session):
    _admin(client, db_session)
    cat = client.post("/api/categories", json={"name": "Продукты", "parent_id": None}).json()
    p = _make_product(client, category_id=cat["id"], characteristic={"вес": "1л"})
    client.patch(f"/api/products/{p['id']}", json={"price": 95.0})
    for url in [
        "/products", "/products/new", f"/products/{p['id']}", f"/products/{p['id']}/edit",
        "/categories", "/categories/new", f"/categories/{cat['id']}/edit",
    ]:
        r = client.get(url)
        assert r.status_code == 200, url
    r = client.get("/products/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/csv")


def test_product_detail_renders_and_page_delete_removes_from_list(client, db_session):
    _admin(client, db_session)
    p = _make_product(client)
    r = client.get(f"/products/{p['id']}")
    assert r.status_code == 200
    assert "Молоко" in r.text
    r = client.post(f"/products/{p['id']}/delete", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/products")
    assert "MILK-1" not in r.text


def test_stock_page_action_updates_rest(client, db_session):
    _admin(client, db_session)
    p = _make_product(client)
    r = client.post(f"/products/{p['id']}/stock", data={"rest": 7}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/api/products/{p['id']}").json()["rest"] == 7


def test_bulk_category_page_action(client, db_session):
    _admin(client, db_session)
    cat = client.post("/api/categories", json={"name": "Напитки", "parent_id": None}).json()
    p = _make_product(client)
    r = client.post(
        "/products/bulk-category",
        data={"product_ids": [p["id"]], "category_id": cat["id"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert client.get(f"/api/products/{p['id']}").json()["category_id"] == cat["id"]


def test_product_new_page_shows_min_stock_field(client, db_session):
    _admin(client, db_session)
    r = client.get("/products/new")
    assert r.status_code == 200
    assert 'name="min_stock"' in r.text


def test_product_edit_page_shows_current_min_stock(client, db_session):
    _admin(client, db_session)
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-F1", "min_stock": 8}).json()["id"]
    r = client.get(f"/products/{pid}/edit")
    assert r.status_code == 200
    assert 'name="min_stock"' in r.text
    assert 'value="8"' in r.text


def test_product_edit_page_min_stock_empty_when_not_set(client, db_session):
    _admin(client, db_session)
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-F2"}).json()["id"]
    r = client.get(f"/products/{pid}/edit")
    assert r.status_code == 200
    assert 'name="min_stock" type="number" min="0" value=""' in r.text


def test_product_edit_page_renders_zero_min_stock(client, db_session):
    _admin(client, db_session)
    pid = client.post("/api/products", json={"name": "Гвозди", "sku": "NAIL-Z1", "min_stock": 0}).json()["id"]
    r = client.get(f"/products/{pid}/edit")
    assert r.status_code == 200
    assert 'name="min_stock" type="number" min="0" value="0"' in r.text


def test_categories_page_offers_where_to_move_the_contents(client, db_session):
    _admin(client, db_session)
    client.post("/api/categories", json={"name": "Продукты", "parent_id": None})
    r = client.get("/categories")
    assert r.status_code == 200
    assert 'id="categories-data"' in r.text
    assert "resolve=true" in r.text
    assert "reassign_to=" in r.text
    assert "onclick" not in r.text
