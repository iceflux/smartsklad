from app.modules.auth.models import Role, User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.inventory.service import DEFAULT_UNITS, get_unit_by_name
from app.modules.orders.service import create_order

PASSWORD = "password123"
FRESH = "Килограмм-тест"


def _user(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, username, PASSWORD).token)
    return u


def _admin(client, db_session):
    return _user(client, db_session, "boss", "admin")


def _seller(client, db_session):
    return _user(client, db_session, "sel", "seller")


def _no_products_view(client, db_session):
    role = Role(name="только-заказы", is_system=False, is_super=False, permissions=["orders.view"])
    db_session.add(role)
    db_session.commit()
    u = User(username="zak", password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "zak", PASSWORD).token)
    return u


def _names(client):
    return [u["name"] for u in client.get("/api/units").json()]


def test_admin_can_create_and_list(client, db_session):
    _admin(client, db_session)
    created = client.post("/api/units", json={"name": FRESH})
    assert created.status_code == 200
    assert created.json()["name"] == FRESH
    assert FRESH in _names(client)


def test_list_refuses_anonymous(client):
    assert client.get("/api/units").status_code == 401


def test_seller_reads_the_catalogue_but_cannot_write(client, db_session):
    _seller(client, db_session)
    listed = client.get("/api/units")
    assert listed.status_code == 200
    assert "кг" in [u["name"] for u in listed.json()]
    assert client.post("/api/units", json={"name": FRESH}).status_code == 403


def test_read_refused_without_products_view(client, db_session):
    _no_products_view(client, db_session)
    assert client.get("/api/units").status_code == 403


def test_seller_cannot_rename_or_delete(client, db_session):
    _seller(client, db_session)
    unit_id = get_unit_by_name(db_session, "г").id
    assert client.patch(f"/api/units/{unit_id}", json={"name": FRESH}).status_code == 403
    assert client.delete(f"/api/units/{unit_id}").status_code == 403


def test_duplicate_is_rejected(client, db_session):
    _admin(client, db_session)
    assert client.post("/api/units", json={"name": FRESH}).status_code == 200
    rejected = client.post("/api/units", json={"name": "  килограмм-ТЕСТ  "})
    assert rejected.status_code == 422
    assert "уже есть" in rejected.json()["detail"]


def test_patch_renames_the_unit(client, db_session):
    _admin(client, db_session)
    unit_id = client.post("/api/units", json={"name": FRESH}).json()["id"]
    renamed = client.patch(f"/api/units/{unit_id}", json={"name": "Килограмм-другой"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Килограмм-другой"
    assert FRESH not in _names(client)
    assert "Килограмм-другой" in _names(client)


def test_delete_takes_the_unit_out_of_the_catalogue(client, db_session):
    _admin(client, db_session)
    unit_id = client.post("/api/units", json={"name": FRESH}).json()["id"]
    assert client.delete(f"/api/units/{unit_id}").status_code == 200
    assert FRESH not in _names(client)


def test_delete_in_use_is_rejected(client, db_session):
    _admin(client, db_session)
    unit_id = client.post("/api/units", json={"name": FRESH}).json()["id"]
    db_session.add(Product(name="Гвоздь", sku="S1", unit_id=unit_id, price=0.0, rest=0))
    db_session.commit()
    rejected = client.delete(f"/api/units/{unit_id}")
    assert rejected.status_code == 422
    assert "используется товарами" in rejected.json()["detail"]
    assert FRESH in _names(client)


def test_rename_leaves_order_history_untouched(client, db_session):
    _admin(client, db_session)
    unit_id = client.post("/api/units", json={"name": FRESH}).json()["id"]
    product = Product(name="Гвоздь", sku="S2", unit_id=unit_id, price=10.0, rest=5)
    customer = Customer(name="ООО Строй")
    db_session.add_all([product, customer])
    db_session.commit()
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": product.id, "quantity": 2, "discount": 0.0}])
    assert order.items[0].unit_snapshot == FRESH
    assert client.patch(f"/api/units/{unit_id}", json={"name": "Килограмм-новый"}).status_code == 200
    db_session.expire_all()
    assert db_session.get(Product, product.id).unit == "Килограмм-новый"
    assert order.items[0].unit_snapshot == FRESH


def test_catalogue_excludes_soft_deleted_units(client, db_session):
    _admin(client, db_session)
    unit_id = client.post("/api/units", json={"name": FRESH}).json()["id"]
    assert client.delete(f"/api/units/{unit_id}").status_code == 200
    listed = client.get("/api/units").json()
    assert [u["name"] for u in listed] == sorted(u["name"] for u in listed)
    assert len(listed) == len(DEFAULT_UNITS)
