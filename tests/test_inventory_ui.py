import json
import re

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


def test_the_product_card_shows_the_minimum_stock_and_where_it_comes_from(client, db_session):
    """Карточка показывает «Минимальный запас», а если его нет — откуда берётся.

    Раньше значение можно было заполнить в форме товара и потом нигде не увидеть,
    то есть сохранить и не проверить. Три состояния различаются не украшением,
    а смыслом расчёта, поэтому проверяются все три:

    * NULL — своего минимума нет, порог считается из «Запас в днях»;
    * 0 — страховка отключена осознанно, и «Запас в днях» на этот товар не действует;
    * число — своя страховка в единицах.
    """
    _admin(client, db_session)

    unset = _make_product(client, sku="MS-NONE")
    zero = _make_product(client, sku="MS-ZERO", min_stock=0)
    own = _make_product(client, sku="MS-OWN", min_stock=25)

    page = client.get(f"/products/{unset['id']}").text
    assert "Не задан" in page, "пустой минимальный запас должен называться прямо"
    assert "Запас в днях" in page, "пустое значение обязано сказать, откуда возьмётся порог"

    page = client.get(f"/products/{zero['id']}").text
    assert "страховка не нужна" in page, (
        "ноль — это осознанный отказ от страховки, а не отсутствие значения")
    assert "задано явно" in page, (
        "без этих слов неясно, почему «Запас в днях» не действует на этот товар")

    page = client.get(f"/products/{own['id']}").text
    assert "25" in page, "заданный минимальный запас должен быть виден числом"


def test_the_product_card_names_the_current_days_setting_not_the_default(client, db_session):
    """В подписи стоит то «Запас в днях», которое сейчас в настройках.

    Число дней берётся из настроек, а не из кода, иначе после смены настройки
    страница продолжала бы называть 14 дней при фактических 30 — и человек
    посчитал бы по ней неверный порог.
    """
    from app.modules.settings.service import MIN_STOCK_DAYS_KEY, set_setting

    _admin(client, db_session)
    set_setting(db_session, MIN_STOCK_DAYS_KEY, "30")
    db_session.expire_all()

    product = _make_product(client, sku="MS-DAYS")
    page = client.get(f"/products/{product['id']}").text
    assert "(30 дней)" in page, "подпись обязана называть текущее число дней из настроек"
    assert "(14 дней)" not in page, (
        "в подписи осталось значение по умолчанию — настройку сменили, а страница "
        "об этом не знает")


BELOW_MIN = "Ниже минимума в карточке"


def test_the_products_list_marks_a_product_below_its_own_minimum(client, db_session):
    """В списке товаров остаток сравнивается с минимумом ИЗ КАРТОЧКИ.

    Именно из карточки, то есть по сырому `min_stock`, а не по посчитанному
    порогу прогноза: список строится на остатке и карточке, средние продажи
    здесь не считаются. Порог «Запас в днях» живёт на прогнозе, и справка
    говорит, что это два разных порога.
    """
    _admin(client, db_session)
    low = _make_product(client, sku="LOW", rest=5, min_stock=10)
    enough = _make_product(client, sku="ENOUGH", rest=15, min_stock=10)
    unset = _make_product(client, sku="UNSET", rest=1)

    page = client.get("/products").text
    assert BELOW_MIN in page, "товар с остатком ниже минимума должен быть помечен"

    # Помечен только тот, у кого помечен именно он: у «ENOUGH» и у товара без
    # минимума отметки быть не должно, иначе строка в списке ничего не значит.
    rows = _rest_cells(page)
    assert rows["LOW"].endswith(BELOW_MIN), "отметка должна стоять у нужного товара"
    assert BELOW_MIN not in rows["ENOUGH"], "остаток выше минимума — отметки быть не должно"
    assert BELOW_MIN not in rows["UNSET"], (
        "без своего минимума товар не помечается: считать не с чем")


def test_the_below_minimum_mark_shows_in_the_order_picker_window(client, db_session):
    """Отметка видна и в окне выбора товара в заказе.

    Это решение владельца, а не побочный эффект: менеджер замечает, что товар
    на исходе, ещё до добавления позиции. Список и окно рисует один кусок
    шаблона, поэтому отметка приходит вместе с ним — и проверка закрепляет
    именно это, иначе следующая правка partial уберёт её молча.
    """
    _admin(client, db_session)
    _make_product(client, sku="LOW", rest=5, min_stock=10)
    window = client.get("/api/lookup/products/table").text
    assert BELOW_MIN in window, (
        "в окне выбора товара отметки нет — решение владельца нарушено")


def _rest_cells(page_html):
    """Остаток каждого товара по его артикулу: строки таблицы → {sku: ячейка}.

    Колонка берётся по позиции заголовка, а не по номеру: у страницы товаров
    впереди стоит ячейка с галочкой, а в окне выбора её нет, так что «шестая
    ячейка» означала бы разные колонки в двух разных таблицах.
    """
    def plain(cell):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()

    header = re.search(r"<thead>(.*?)</thead>", page_html, re.S)
    heads = [plain(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", header.group(1), re.S)]
    sku_at = heads.index("Артикул")
    rest_at = heads.index("Остаток")

    out = {}
    body = re.search(r"<tbody>(.*?)</tbody>", page_html, re.S)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) <= rest_at:
            continue
        out[plain(cells[sku_at])] = plain(cells[rest_at])
    return out


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
    root = client.post("/api/categories", json={"name": "Продукты", "parent_id": None}).json()
    child = client.post(
        "/api/categories", json={"name": "Молочное", "parent_id": root["id"]}
    ).json()
    r = client.get("/categories")
    assert r.status_code == 200
    assert "resolve=true" in r.text
    assert "reassign_to=" in r.text
    assert "onclick" not in r.text
    blob = re.search(
        r'<script id="categories-data" type="application/json">(.*?)</script>', r.text, re.S
    )
    assert blob is not None
    tree = json.loads(blob.group(1))
    assert tree == [
        {
            "children": [{"id": child["id"], "name": "Молочное"}],
            "id": root["id"],
            "name": "Продукты",
            "parent_id": None,
        }
    ]
