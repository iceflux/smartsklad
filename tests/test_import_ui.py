import json
import re
from urllib.parse import parse_qs, urlencode, urlparse

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


def _write(tmp_path, name, body):
    (tmp_path / name).write_bytes(
        b"\xef\xbb\xbf" + json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _export(tmp_path, payload=None):
    _write(tmp_path, "export.json",
           payload or {"products": [{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
                       "customers": [], "orders": []})


def _use_folder(monkeypatch, tmp_path):
    monkeypatch.setattr("app.main.import_path", lambda db: str(tmp_path))


def _msg(response):
    return parse_qs(urlparse(response.headers["location"]).query).get("msg", [""])[0]


def _substitute_ids(html, raw_unit):
    block = html.split(f'name="unit_map[{raw_unit}]"')[1].split("</select>")[0]
    return re.findall(r'<option value="(\d+)">', block)


def _create_option_value(html, raw_unit):
    block = html.split(f'name="unit_map[{raw_unit}]"')[1].split("</select>")[0]
    return re.search(r'<option value="([^"]*)"', block).group(1)


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
    from app.modules.inventory.models import Product
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    response = client.post("/import/run", content=b"filenames=nope.json", headers=FORM,
                           follow_redirects=False)
    assert response.status_code == 303
    assert "%D0%9E%D1%88%D0%B8%D0%B1%D0%BA%D0%B0" in response.headers["location"]
    assert db_session.query(Product).count() == 0


def test_run_post_reports_skipped_orders_and_warnings(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path, {
        "products": [{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
        "customers": [{"id": "c1", "name": "Ромашка"}],
        "orders": [
            {"id": "o1", "number": "1", "date": "2026-01-05 10:00:00", "customer_id": "c1",
             "items": [{"product_id": "p1", "name": "Гвоздь", "unit": "шт",
                        "quantity": 2, "price": 10}]},
            {"id": "o2", "number": "2", "date": "не дата", "customer_id": "c1", "items": []},
        ],
    })
    response = client.post("/import/run", content=b"filenames=export.json", headers=FORM,
                           follow_redirects=False)
    assert response.status_code == 303
    assert _msg(response) == ("Импорт завершён: товаров 1, клиентов 1, заказов 1, "
                              "пропущено заказов 1, предупреждений 1")


def test_preview_post_rejects_empty_selection(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    response = client.post("/import/preview", content=b"", headers=FORM,
                           follow_redirects=False)
    assert response.status_code == 303
    assert "не выбрано ни одного файла" in _msg(response)


def test_run_post_rejects_empty_selection(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    response = client.post("/import/run", content=b"", headers=FORM, follow_redirects=False)
    assert response.status_code == 303
    assert "не выбрано ни одного файла" in _msg(response)
    assert db_session.query(Product).count() == 0


def test_two_files_are_all_imported(client, db_session, tmp_path, monkeypatch):
    from app.modules.customers.models import Customer
    from app.modules.inventory.models import Product
    from app.modules.orders.models import Order
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _write(tmp_path, "export.json",
           {"products": [{"id": "p1", "name": "Гвоздь", "unit": "шт"}],
            "customers": [], "orders": []})
    _write(tmp_path, "more.json",
           {"products": [], "customers": [{"id": "c1", "name": "Ромашка"}],
            "orders": [{"id": "o1", "number": "1", "date": "2026-01-05 10:00:00",
                        "customer_id": "c1",
                        "items": [{"product_id": "p1", "name": "Гвоздь", "unit": "шт",
                                   "quantity": 2, "price": 10}]}]})
    body = urlencode([("filenames", "export.json"), ("filenames", "more.json")]).encode()
    preview = client.post("/import/preview", content=body, headers=FORM)
    assert preview.status_code == 200
    assert "Товаров создать</dt><dd>1</dd>" in preview.text
    assert "Клиентов создать</dt><dd>1</dd>" in preview.text
    assert "Заказов создать</dt><dd>1</dd>" in preview.text
    response = client.post("/import/run", content=body, headers=FORM, follow_redirects=False)
    assert response.status_code == 303
    assert _msg(response) == ("Импорт завершён: товаров 1, клиентов 1, заказов 1, "
                              "пропущено заказов 0, предупреждений 0")
    assert db_session.query(Product).count() == 1
    assert db_session.query(Customer).count() == 1
    assert db_session.query(Order).count() == 1


def test_run_post_does_not_block_the_event_loop(client, db_session, tmp_path, monkeypatch):
    import asyncio
    import time

    import httpx

    import app.main as main

    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path)
    finished = asyncio.Event()
    real_run = main.run_import

    def slow_run(*args, **kwargs):
        time.sleep(0.5)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(main, "run_import", slow_run)

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while not finished.is_set():
                await asyncio.sleep(0.01)
                ticks += 1

        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     cookies={"session": client.cookies["session"]}) as ac:
            watch = asyncio.create_task(ticker())
            try:
                await ac.post("/import/run", content=b"filenames=export.json", headers=FORM)
            finally:
                finished.set()
                await watch
        return ticks

    assert asyncio.run(scenario()) > 20


def _export_unknown_unit(tmp_path, unit="килограмм"):
    _export(tmp_path, {"products": [{"id": "p1", "name": "Гвоздь", "unit": unit}],
                       "customers": [], "orders": []})


def test_preview_shows_unit_resolution(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    response = client.post("/import/preview", content=b"filenames=export.json", headers=FORM)
    assert response.status_code == 200
    assert "килограмм" in response.text
    assert "name=\"unit_map[" in response.text
    assert '<option value="new:">' in response.text
    assert 'name="unit_name[килограмм]" value="килограмм"' in response.text


def test_unresolved_units_hide_the_run_form(client, db_session, tmp_path, monkeypatch):
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    response = client.post("/import/preview", content=b"filenames=export.json", headers=FORM)
    assert 'action="/import/run"' not in response.text
    assert "Импортировать" not in response.text


def test_run_refused_while_unit_unresolved(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    response = client.post("/import/run", content=b"filenames=export.json", headers=FORM,
                           follow_redirects=False)
    assert response.status_code == 303
    assert "не заданы единицы: килограмм" in _msg(response)
    assert db_session.query(Product).count() == 0


def test_unit_map_creates_unit_and_unblocks_import(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product, Unit
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[килограмм]", "new:Килограмм")]).encode()
    response = client.post("/import/run", content=body, headers=FORM, follow_redirects=False)
    assert response.status_code == 303
    assert "Ошибка" not in _msg(response)
    assert db_session.query(Product).one().unit_rel.name == "Килограмм"
    assert db_session.query(Unit).filter(Unit.name == "Килограмм").count() == 1


def test_repreview_with_create_decision_writes_nothing(client, db_session, tmp_path, monkeypatch):
    from app.modules.audit.models import AuditLog
    from app.modules.inventory.models import Unit
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    units_before = db_session.query(Unit).count()
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[килограмм]", "new:Килограмм-новый")]).encode()
    response = client.post("/import/preview", content=body, headers=FORM)
    assert response.status_code == 200
    assert 'name="unit_map[килограмм]" value="new:Килограмм-новый"' in response.text
    assert 'action="/import/run"' in response.text
    assert db_session.query(Unit).count() == units_before
    assert db_session.query(Unit).filter(Unit.name == "Килограмм-новый").count() == 0
    assert db_session.query(AuditLog).filter(AuditLog.action == "unit.create").count() == 0


def test_new_unit_name_field_overrides_the_option_default(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[килограмм]", "new:"),
                      ("unit_name[килограмм]", "Килограмм")]).encode()
    preview = client.post("/import/preview", content=body, headers=FORM)
    assert 'name="unit_map[килограмм]" value="new:Килограмм"' in preview.text
    response = client.post("/import/run", content=body, headers=FORM, follow_redirects=False)
    assert "Ошибка" not in _msg(response)
    assert db_session.query(Product).one().unit_rel.name == "Килограмм"


def test_cleared_new_unit_name_refuses_the_import(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product, Unit
    from app.modules.inventory.service import DEFAULT_UNITS
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    html = client.post("/import/preview", content=b"filenames=export.json", headers=FORM).text
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[килограмм]", _create_option_value(html, "килограмм"))]).encode()
    preview = client.post("/import/preview", content=body, headers=FORM,
                          follow_redirects=False)
    assert preview.status_code == 303
    assert "название единицы обязательно" in _msg(preview)
    response = client.post("/import/run", content=body, headers=FORM, follow_redirects=False)
    assert response.status_code == 303
    assert "название единицы обязательно" in _msg(response)
    assert db_session.query(Product).count() == 0
    assert db_session.query(Unit).count() == len(DEFAULT_UNITS)


def test_partial_decision_refused_and_writes_nothing(client, db_session, tmp_path, monkeypatch):
    from app.modules.audit.models import AuditLog
    from app.modules.inventory.models import Product, Unit
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path, {"products": [{"id": "p1", "name": "Гвоздь", "unit": "килограмм"},
                                     {"id": "p2", "name": "Доска", "unit": "кубометр"}],
                       "customers": [], "orders": []})
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[килограмм]", "new:Килограмм-новый")]).encode()
    response = client.post("/import/run", content=body, headers=FORM, follow_redirects=False)
    assert response.status_code == 303
    assert "не заданы единицы" in _msg(response)
    assert db_session.query(Product).count() == 0
    assert db_session.query(Unit).filter(Unit.name == "Килограмм-новый").count() == 0
    assert db_session.query(AuditLog).filter(AuditLog.action == "unit.create").count() == 0
    assert db_session.query(AuditLog).filter(AuditLog.action == "import.run").count() == 0


def test_tricky_unit_names_survive_the_round_trip(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product, Unit
    from app.modules.inventory.service import get_unit_by_name
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path, {"products": [{"id": "p1", "name": "Гвоздь", "unit": "1 000 шт"},
                                     {"id": "p2", "name": "Доска", "unit": "куб.м"}],
                       "customers": [], "orders": []})
    preview = client.post("/import/preview", content=b"filenames=export.json", headers=FORM)
    assert 'name="unit_map[1 000 шт]"' in preview.text
    assert 'name="unit_map[куб.м]"' in preview.text
    assert 'name="unit_name[1 000 шт]" value="1 000 шт"' in preview.text
    assert 'name="unit_name[куб.м]" value="куб.м"' in preview.text
    substitute = get_unit_by_name(db_session, "м3").id
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[1 000 шт]", "new:"),
                      ("unit_name[1 000 шт]", "1 000 шт"),
                      ("unit_map[куб.м]", str(substitute))]).encode()
    repreview = client.post("/import/preview", content=body, headers=FORM)
    assert 'name="unit_map[1 000 шт]" value="new:1 000 шт"' in repreview.text
    assert 'name="unit_map[куб.м]" value="%s"' % substitute in repreview.text
    response = client.post("/import/run", content=body, headers=FORM, follow_redirects=False)
    assert "Ошибка" not in _msg(response)
    units = {p.name: p.unit_rel.name for p in db_session.query(Product).all()}
    assert units == {"Гвоздь": "1 000 шт", "Доска": "м3"}


def test_applied_decisions_are_listed_for_the_owner(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.service import get_unit_by_name
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export(tmp_path, {"products": [{"id": "p1", "name": "Гвоздь", "unit": "килограмм"},
                                     {"id": "p2", "name": "Саморез", "unit": "шт"}],
                       "customers": [], "orders": []})
    before = client.post("/import/preview", content=b"filenames=export.json", headers=FORM)
    assert "Применённые единицы измерения" not in before.text
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[килограмм]", str(get_unit_by_name(db_session, "кг").id))]).encode()
    response = client.post("/import/preview", content=body, headers=FORM)
    assert "Применённые единицы измерения" in response.text
    assert '1С: килограмм → <span class="font-semibold">кг</span>' in response.text
    assert '1С: шт → <span class="font-semibold">шт</span>' in response.text
    assert "Чтобы изменить выбор" in response.text
    assert response.text.index('1С: килограмм') < response.text.index('action="/import/run"')


def test_substitute_dropdown_offers_the_catalogue(client, db_session, tmp_path, monkeypatch):
    from app.modules.inventory.models import Product, Unit
    from app.modules.inventory.service import get_unit_by_name
    _admin(client, db_session)
    _use_folder(monkeypatch, tmp_path)
    _export_unknown_unit(tmp_path)
    html = client.post("/import/preview", content=b"filenames=export.json", headers=FORM).text
    offered = _substitute_ids(html, "килограмм")
    catalogue = db_session.query(Unit).filter(Unit.is_deleted.is_(False)).count()
    assert len(offered) == catalogue
    assert str(get_unit_by_name(db_session, "м3").id) in offered
    body = urlencode([("filenames", "export.json"),
                      ("unit_map[килограмм]", offered[0])]).encode()
    response = client.post("/import/run", content=body, headers=FORM, follow_redirects=False)
    assert "Ошибка" not in _msg(response)
    assert db_session.query(Product).one().unit_rel.name == db_session.get(Unit, int(offered[0])).name


def test_navigation_contains_import(client, db_session):
    _admin(client, db_session)
    text = client.get("/products").text
    assert 'href="/import"' in text
    assert ">Импорт</a>" in text


def test_navigation_hides_import_for_seller(client, db_session):
    _seller(client, db_session)
    assert 'href="/import"' not in client.get("/products").text
