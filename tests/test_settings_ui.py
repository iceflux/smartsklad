from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


def _login(client, db_session, username, role):
    u = User(username=username, password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, role).id)
    db_session.add(u)
    db_session.commit()
    s = login(db_session, username, PASSWORD)
    client.cookies.set("session", s.token)


def _admin(client, db_session):
    _login(client, db_session, "boss", "admin")


def test_settings_page_403_without_right(client, db_session):
    _login(client, db_session, "seller1", "seller")  # seller не имеет settings.change
    r = client.get("/settings")
    assert r.status_code == 403


def test_settings_page_admin(client, db_session):
    _admin(client, db_session)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "stock_track_default" not in r.text  # скрываем ключ, показываем лейбл
    assert "Учитывать остатки" in r.text


def test_settings_page_post_changes(client, db_session):
    _admin(client, db_session)
    r = client.post("/settings", data={"track_rest_default": "false"}, follow_redirects=False)
    assert r.status_code == 303
    assert "?msg" in r.headers["location"]


def test_new_product_form_shows_checkbox(client, db_session):
    _admin(client, db_session)
    r = client.get("/products/new")
    assert r.status_code == 200
    assert "Учитывать остатки" in r.text


def test_detail_shows_track_rest(client, db_session):
    _admin(client, db_session)
    r = client.post("/api/products", json={
        "name": "Вирт", "sku": "VIRT-UI", "unit": "шт", "price": 1.0, "rest": 0, "track_rest": False,
    })
    assert r.status_code == 201
    pid = r.json()["id"]
    r2 = client.get(f"/products/{pid}")
    assert r2.status_code == 200
    assert "нет" in r2.text  # «Учёт остатков: нет»