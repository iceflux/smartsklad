from urllib.parse import parse_qs, urlparse

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory.models import Product
from app.modules.inventory.service import DEFAULT_UNITS, get_unit_by_name

PASSWORD = "password123"
FRESH = "Килограмм-тест"
ROW = 'data-unit-id="'


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


def _msg(response):
    return parse_qs(urlparse(response.headers["location"]).query).get("msg", [""])[0]


def _unit_named(db_session, name):
    db_session.expire_all()
    return get_unit_by_name(db_session, name)


def _listed(client, db_session):
    db_session.expire_all()
    return client.get("/units").text.count(ROW)


def test_units_page_requires_login(client):
    response = client.get("/units", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_units_page_forbidden_for_seller(client, db_session):
    _seller(client, db_session)
    assert client.get("/units").status_code == 403


def test_units_page_lists_the_seeded_catalogue(client, db_session):
    _admin(client, db_session)
    response = client.get("/units")
    assert response.status_code == 200
    assert response.text.count(ROW) == len(DEFAULT_UNITS)
    for name in ("кг", "дюжина"):
        assert name in response.text


def test_nav_link_shown_to_admin_and_hidden_from_seller(client, db_session):
    _admin(client, db_session)
    assert 'href="/units"' in client.get("/units").text
    _seller(client, db_session)
    assert 'href="/units"' not in client.get("/products").text


def test_page_create_adds_the_unit(client, db_session):
    _admin(client, db_session)
    response = client.post("/units/new", data={"name": FRESH}, follow_redirects=False)
    assert response.status_code == 303
    assert _msg(response) == "Единица добавлена"
    assert _unit_named(db_session, FRESH) is not None
    assert _listed(client, db_session) == len(DEFAULT_UNITS) + 1


def test_page_create_duplicate_reports_the_reason(client, db_session):
    _admin(client, db_session)
    client.post("/units/new", data={"name": FRESH}, follow_redirects=False)
    response = client.post("/units/new", data={"name": "килограмм-ТЕСТ"}, follow_redirects=False)
    assert _msg(response) == "Ошибка: единица с таким названием уже есть"
    assert _listed(client, db_session) == len(DEFAULT_UNITS) + 1


def test_page_rename(client, db_session):
    _admin(client, db_session)
    unit_id = get_unit_by_name(db_session, "г").id
    response = client.post(f"/units/{unit_id}/edit", data={"name": "Грамм-другой"},
                           follow_redirects=False)
    assert _msg(response) == "Единица переименована"
    assert _unit_named(db_session, "Грамм-другой") is not None
    assert _unit_named(db_session, "г") is None


def test_page_delete(client, db_session):
    _admin(client, db_session)
    unit_id = get_unit_by_name(db_session, "г").id
    response = client.post(f"/units/{unit_id}/delete", follow_redirects=False)
    assert _msg(response) == "Единица удалена"
    assert _unit_named(db_session, "г") is None
    assert _listed(client, db_session) == len(DEFAULT_UNITS) - 1


def test_page_delete_in_use_reports_the_reason(client, db_session):
    _admin(client, db_session)
    unit_id = get_unit_by_name(db_session, "г").id
    db_session.add(Product(name="Гвоздь", sku="S1", unit_id=unit_id, price=0.0, rest=0))
    db_session.commit()
    response = client.post(f"/units/{unit_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert _msg(response) == "Ошибка: единица используется товарами"
    assert _unit_named(db_session, "г") is not None


def test_page_actions_are_closed_to_seller_and_anonymous(client, db_session):
    unit_id = get_unit_by_name(db_session, "г").id
    _seller(client, db_session)
    assert client.post("/units/new", data={"name": FRESH}, follow_redirects=False).status_code == 403
    assert client.post(f"/units/{unit_id}/edit", data={"name": "х"},
                       follow_redirects=False).status_code == 403
    assert client.post(f"/units/{unit_id}/delete", follow_redirects=False).status_code == 403
    client.cookies.clear()
    for path in ("/units/new", f"/units/{unit_id}/edit", f"/units/{unit_id}/delete"):
        response = client.post(path, data={"name": "х"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
