import re
from pathlib import Path

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory import service as inventory_service
from app.modules.inventory.models import Product
from app.modules.inventory.router import ProductIn
from app.modules.inventory.service import create_product, get_unit_by_name, update_product

PASSWORD = "password123"
FORM = Path("app/templates/product_form.html").read_bytes().decode("utf-8")
SERVICE = Path("app/modules/inventory/service.py").read_bytes().decode("utf-8")


def _unit(db):
    return get_unit_by_name(db, "шт").id


def _admin(client, db_session):
    user = User(username="boss", password_hash=hash_password(PASSWORD),
                role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(user)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _make(db, name, sku):
    return create_product(db, name=name, sku=sku, category_id=None,
                          unit_id=_unit(db), price=1.0, characteristic=None, rest=0)


def test_a_product_without_an_article_gets_a_sequential_one(client, db_session):
    _admin(client, db_session)
    response = client.post("/api/products", json={"name": "Гвозди"})
    assert response.status_code == 201, response.text
    assert response.json()["sku"] == "00001"


def test_a_blank_article_is_not_refused_by_the_api(client, db_session):
    _admin(client, db_session)
    response = client.post("/api/products", json={"name": "Гвозди", "sku": "   "})
    assert response.status_code == 201, response.text
    assert response.json()["sku"] == "00001"


def test_the_api_schema_does_not_require_the_article():
    assert ProductIn(name="Гвозди").sku is None, (
        "без артикула схема не собирается — форма без поля упадёт в 422"
    )


def test_the_generated_article_is_a_plain_sequential_number(db_session):
    assert re.fullmatch(r"\d{5}", _make(db_session, "Гвозди", "").sku)


def test_two_products_created_in_a_row_get_two_different_articles(client, db_session):
    _admin(client, db_session)
    first = client.post("/api/products", json={"name": "Гвозди"}).json()["sku"]
    second = client.post("/api/products", json={"name": "Шурупы"}).json()["sku"]
    assert (first, second) == ("00001", "00002")


def test_the_generated_article_skips_the_numbers_already_taken(db_session):
    _make(db_session, "Раз", "00001")
    _make(db_session, "Два", "00003")
    assert _make(db_session, "Три", "").sku == "00002"


def test_a_typed_article_is_never_overwritten(db_session):
    assert _make(db_session, "Гвозди", "Гвоздь-50").sku == "Гвоздь-50"


def test_the_article_is_picked_again_when_another_writer_took_it_first(db_session, monkeypatch):
    """Гонка двух сохранений: генератор прочитал таблицу, номер занял кто-то другой.

    Единственный арбитр — уникальное ограничение базы, поэтому проверка бьёт по
    нему же: без повторной попытки из create_product вылезает IntegrityError.
    """
    _make(db_session, "Чужой", "00001")
    real = inventory_service.SkuFactory
    handed_out = []

    def stale_first(db, *args, **kwargs):
        if not handed_out:
            handed_out.append(True)
            return lambda: "00001"
        return real(db, *args, **kwargs)

    monkeypatch.setattr(inventory_service, "SkuFactory", stale_first)
    product = _make(db_session, "Гвозди", "")
    assert product.sku != "00001"
    assert db_session.query(Product).filter_by(sku="00001").one().name == "Чужой"


def test_a_patch_that_clears_the_article_gets_a_new_one(db_session):
    product = _make(db_session, "Гвозди", "Гвоздь-1")
    update_product(db_session, product.id, {"sku": "   "})
    assert db_session.get(Product, product.id).sku == "00001"


def test_the_api_accepts_a_cleared_article(client, db_session):
    _admin(client, db_session)
    product_id = _make(db_session, "Гвозди", "Гвоздь-1").id
    response = client.patch(f"/api/products/{product_id}", json={"sku": ""})
    assert response.status_code == 200, response.text
    assert response.json()["sku"] == "00001"


def test_the_form_no_longer_requires_the_article(client, db_session):
    _admin(client, db_session)
    body = client.get("/products/new").text
    field = body[body.index('<input name="sku"'):]
    field = field[:field.index(">")]
    assert "required" not in field, "форма всё ещё не отпускает пустой артикул"


def test_the_form_shows_the_next_article_as_a_placeholder(client, db_session):
    _admin(client, db_session)
    _make(db_session, "Раз", "00001")
    assert 'placeholder="00002"' in client.get("/products/new").text


def test_the_editor_of_a_saved_product_shows_no_placeholder(client, db_session):
    _admin(client, db_session)
    product_id = _make(db_session, "Гвозди", "Гвоздь-1").id
    body = client.get(f"/products/{product_id}/edit").text
    assert "placeholder=" not in body[body.index('<input name="sku"'):][:200]


def test_the_generator_is_the_one_the_importer_already_uses():
    assert "from app.modules.importer.resolve import SkuFactory" in SERVICE
    assert ":05d" not in SERVICE, "в inventory/service.py появился второй генератор"
    assert "f\"1C-" not in SERVICE


def test_the_form_sends_the_article_even_when_it_is_blank():
    assert "body.sku = f.sku.value;" in FORM, (
        "форма не отправляет артикул — сервер не увидит, что поле пустое"
    )