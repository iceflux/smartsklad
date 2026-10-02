import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory.models import Product
from app.modules.inventory.service import get_unit_by_name, update_product

PASSWORD = "password123"
FORM = Path("app/templates/product_form.html").read_bytes().decode("utf-8")
BODY = {"name": "Гвозди", "sku": "NAIL-D", "price": 10.0, "rest": 5}
TEXT = "Оцинкованный гвоздь 3×35, пачка 100 шт."


def _admin(client, db_session):
    user = User(username="boss", password_hash=hash_password(PASSWORD),
                role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(user)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _create(client, **extra):
    response = client.post("/api/products", json={**BODY, **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_the_description_is_saved_and_given_back_by_the_api(client, db_session):
    _admin(client, db_session)
    product_id = _create(client, description=TEXT)
    assert client.get(f"/api/products/{product_id}").json()["description"] == TEXT
    assert db_session.get(Product, product_id).description == TEXT


def test_the_description_reaches_the_product_page(client, db_session):
    _admin(client, db_session)
    product_id = _create(client, description=TEXT)
    body = client.get(f"/products/{product_id}").text
    assert "Описание" in body and TEXT in body


def test_a_product_without_a_description_shows_no_block(client, db_session):
    _admin(client, db_session)
    product_id = _create(client)
    assert "Описание" not in client.get(f"/products/{product_id}").text


def test_a_blank_description_is_stored_as_nothing(client, db_session):
    _admin(client, db_session)
    for blank in ("", "   "):
        product_id = _create(client, sku=f"NAIL-B{len(blank)}", description=blank)
        assert client.get(f"/api/products/{product_id}").json()["description"] is None
        assert db_session.get(Product, product_id).description is None


def test_the_description_is_trimmed(client, db_session):
    _admin(client, db_session)
    product_id = _create(client, description=f"  {TEXT}  ")
    assert db_session.get(Product, product_id).description == TEXT


def test_a_patch_can_change_and_clear_the_description(client, db_session):
    _admin(client, db_session)
    product_id = _create(client, description=TEXT)
    assert client.patch(f"/api/products/{product_id}",
                        json={"description": "Другой текст"}).json()["description"] == "Другой текст"
    assert client.patch(f"/api/products/{product_id}",
                        json={"description": ""}).json()["description"] is None


def test_a_patch_that_never_mentions_the_description_keeps_it(db_session):
    product = Product(name="Гвозди", sku="NAIL-S", unit_id=get_unit_by_name(db_session, "шт").id,
                      price=1.0, description=TEXT)
    db_session.add(product)
    db_session.commit()
    update_product(db_session, product.id, {"name": "Гвозди оцинкованные"})
    assert db_session.get(Product, product.id).description == TEXT


def test_the_change_of_the_description_is_audited(client, db_session):
    _admin(client, db_session)
    product_id = _create(client)
    client.patch(f"/api/products/{product_id}", json={"description": TEXT})
    written = [row.new_value for row in db_session.query(AuditLog)
               .filter(AuditLog.entity_type == "product").all()]
    assert any(TEXT in (value or "") for value in written)


def test_the_form_offers_the_description(client, db_session):
    _admin(client, db_session)
    assert '<textarea name="description"' in client.get("/products/new").text


def test_the_form_sends_the_description(client, db_session):
    _admin(client, db_session)
    product_id = _create(client, description=TEXT)
    body = client.get(f"/products/{product_id}/edit").text
    assert f">{TEXT}</textarea>" in body, "сохранённое описание не вернулось в форму"
    assert "body.description = f.description.value;" in body, (
        "форма не отправляет описание — поле есть, а сохранить его нечем"
    )


def test_the_description_is_disabled_for_a_role_that_only_changes_the_price(client, db_session):
    _admin(client, db_session)
    product_id = _create(client, description=TEXT)
    db_session.add(User(username="chief", password_hash=hash_password(PASSWORD),
                        role_id=get_role_by_name(db_session, "manager").id))
    db_session.commit()
    client.cookies.set("session", login(db_session, "chief", PASSWORD).token)
    body = client.get(f"/products/{product_id}/edit").text
    assert body, "страница редактирования должна открываться тому, кому можно менять цену"
    field = body[body.index('<textarea name="description"'):
                 body.index('<textarea name="characteristic"')]
    assert "disabled" in field, "описание правится без права products.edit"


def test_the_json_field_is_the_last_one_in_the_form():
    names = [(match.start(), match.group(1)) for match in
             re.finditer(r'<(?:input|select|textarea)[^>]*\sname="([^"]+)"', FORM)]
    last = names[-1]
    assert last[1] == "characteristic", (
        f"последнее поле формы — {last[1]!r}, а характеристики должны быть в самом конце"
    )
    assert '<textarea name="description"' in FORM
    assert FORM.index('name="description"') < last[0]
    assert "Характеристики (JSON)" in FORM, "подпись поля, которое владелец назвал, поехала"


def test_the_model_and_the_migration_agree_on_the_column():
    column = Product.__table__.c.description
    assert column.nullable and column.type.python_type is str
    assert Product.__table__.c.description.type.__class__.__name__ == "Text"


def test_exactly_one_revision_of_the_chain_adds_the_description_column():
    """Тестовая база строится из моделей, а не из миграций.

    Ошибка в ревизии оставила бы весь прогон зелёным и упала бы на рабочей
    базе, поэтому цепочка читается здесь же: ровно одна ревизия добавляет
    колонку, и добавляет её как Text и nullable.
    """
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(script.get_heads()) == 1, "цепочка миграций разошлась"
    adding = [revision for revision in script.walk_revisions()
              if re.search(r"add_column\(\s*['\"]products['\"][^)]*['\"]description['\"]",
                           Path(revision.path).read_bytes().decode("utf-8"), re.S)]
    assert len(adding) == 1, f"колонку description добавляют ревизии {[r.revision for r in adding]}"
    body = Path(adding[0].path).read_bytes().decode("utf-8")
    assert "sa.Text()" in body and "nullable=True" in body
    assert "op.drop_column('products', 'description')" in body