import json

import pytest

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login

PASSWORD = "password123"


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
    body = payload or {"products": [{"id": "p1", "name": "Гвоздь", "unit": "шт"}]}
    (tmp_path / "export.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _use_folder(monkeypatch, tmp_path):
    monkeypatch.setattr("app.modules.importer.router.import_path", lambda db: str(tmp_path))


def test_files_forbidden_for_seller(client, db_session):
    _seller(client, db_session)
    assert client.get("/api/import/files").status_code == 403


def test_files_rejects_anonymous(client):
    assert client.get("/api/import/files").status_code in (401, 403)


def test_files_lists_json(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    response = client.get("/api/import/files")
    assert response.status_code == 200
    assert [f["name"] for f in response.json()["files"]] == ["export.json"]


def test_preview_reports_counts(client, db_session, tmp_path, monkeypatch):
    from app.modules.customers.models import Customer
    from app.modules.inventory.models import Product
    from app.modules.orders.models import Order
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path, {
        "products": [{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        "customers": [{"id": "c1", "name": "ООО Ромашка"}],
        "orders": [{"id": "o1", "date": "2026-01-15 10:00:00", "customer_id": "c1",
                    "items": [{"product_id": "p1", "quantity": 2, "price": 10.0}]}],
    })
    response = client.post("/api/import/preview", json={"filenames": ["export.json"]})
    assert response.status_code == 200
    body = response.json()
    assert body["products"]["created"] == 1
    assert body["customers"]["created"] == 1
    assert body["orders"]["created"] == 1
    assert db_session.query(Product).count() == 0
    assert db_session.query(Customer).count() == 0
    assert db_session.query(Order).count() == 0


def test_preview_rejects_traversal(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    response = client.post("/api/import/preview", json={"filenames": ["../escape.json"]})
    assert response.status_code == 422


def test_run_creates_and_reports(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    response = client.post("/api/import/run", json={"filenames": ["export.json"]})
    assert response.status_code == 200
    assert response.json()["products"]["created"] == 1
    assert db_session.query(Product).count() == 1


def test_set_path_rejects_empty(client, db_session):
    _admin(client, db_session)
    response = client.put("/api/import/path", json={"path": "  "})
    assert response.status_code == 422


def test_files_reports_current_folder(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    assert client.get("/api/import/files").json()["folder"] == str(tmp_path)


@pytest.mark.parametrize("method,url,payload", [
    ("get", "/api/import/files", {}),
    ("post", "/api/import/preview", {"filenames": []}),
    ("post", "/api/import/run", {"filenames": []}),
    ("put", "/api/import/path", {"path": "x"}),
])
def test_every_endpoint_forbidden_for_seller(client, db_session, method, url, payload):
    _seller(client, db_session)
    assert client.request(method, url, json=payload).status_code == 403


def test_run_rejects_traversal(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    assert client.post("/api/import/run", json={"filenames": ["../escape.json"]}).status_code == 422


def test_run_translates_other_value_error_to_400(client, db_session, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("сломалось")

    _admin(client, db_session)
    monkeypatch.setattr("app.modules.importer.router.run_import", boom)
    response = client.post("/api/import/run", json={"filenames": ["export.json"]})
    assert response.status_code == 400
