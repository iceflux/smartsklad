import json
from urllib.parse import urlencode

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.settings.service import get_setting

PASSWORD = "password123"
FORM = {"content-type": "application/x-www-form-urlencoded"}


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


def _export(tmp_path, payload=None):
    body = payload or {"products": [{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
                       "customers": [], "orders": []}
    (tmp_path / "export.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _use_folder(monkeypatch, tmp_path):
    monkeypatch.setattr("app.main.import_path", lambda db: str(tmp_path))


def test_page_redirects_anonymous(client):
    response = client.get("/import", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_page_forbidden_for_seller(client, db_session):
    _seller(client, db_session)
    assert client.get("/import").status_code == 403


def test_post_routes_forbidden_for_seller(client, db_session):
    _seller(client, db_session)
    for path in ("/import/path", "/import/preview", "/import/run"):
        assert client.post(path, content=b"", headers=FORM).status_code == 403, path


def test_page_lists_files(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    response = client.get("/import")
    assert response.status_code == 200
    assert "export.json" in response.text


def test_page_warns_about_zero_stock(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    response = client.get("/import")
    assert "остатки товаров останутся нулевыми" in response.text
    assert "новые заказы на эти товары будут отклонены" in response.text


def test_path_post_saves_setting(client, db_session, tmp_path):
    _admin(client, db_session)
    response = client.post("/import/path", content=urlencode({"path": str(tmp_path)}).encode(),
                           headers=FORM, follow_redirects=False)
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]
    assert get_setting(db_session, "import_path") == str(tmp_path)


def test_path_post_rejects_empty_path(client, db_session, tmp_path):
    _admin(client, db_session)
    response = client.post("/import/path", content=b"path=", headers=FORM,
                           follow_redirects=False)
    assert response.status_code == 303
    assert "%D0%9E%D1%88%D0%B8%D0%B1%D0%BA%D0%B0" in response.headers["location"]
    assert get_setting(db_session, "import_path") is None


def test_preview_post_shows_report(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    response = client.post("/import/preview", content=b"filenames=export.json", headers=FORM)
    assert response.status_code == 200
    assert "Разбор" in response.text
    assert "Товаров создать</dt><dd>1</dd>" in response.text
    assert db_session.query(Product).count() == 0


def test_run_post_redirects_with_message(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    response = client.post("/import/run", content=b"filenames=export.json", headers=FORM,
                           follow_redirects=False)
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]
    assert db_session.query(Product).count() == 1


def test_run_post_reports_bad_file(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    response = client.post("/import/run", content=b"filenames=nope.json", headers=FORM,
                           follow_redirects=False)
    assert response.status_code == 303
    assert "%D0%9E%D1%88%D0%B8%D0%B1%D0%BA%D0%B0" in response.headers["location"]


def test_navigation_contains_import(client, db_session):
    _admin(client, db_session)
    text = client.get("/products").text
    assert 'href="/import"' in text
    assert ">Импорт</a>" in text
