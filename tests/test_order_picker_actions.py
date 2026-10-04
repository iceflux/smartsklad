"""Две кнопки у строки заказа: «Создать товар» и «Выбрать товар».

Обе живут в блоке подстановки товара, а не на странице товаров: опечатку в
названии человек правит, не закрывая заказ, а выбрать из тысяч строк можно
просмотром, а не набором.

Про права — отдельно, потому что это требование владельца словами: если права
нет, человек должен узнать, КАКОГО именно права ему не хватает, и по-человечески.
Поэтому подпись берётся из `PERMISSION_LABELS` (человек не может действовать на
код `products.edit`), и проверка запрещает и код, и захардкоженную в шаблоне
подпись.

Кнопка не прячется, а гасится, и вот почему: владелец просил уведомление, а
уведомление у спрятанной кнопки недостижимо — нажать нечего. Право выдаётся на
странице «Роли», то есть может появиться завтра, и молчаливая кнопка сделала бы
функцию ненаходимой.

Клиентская половина (создание, выбор из окна, Escape) — в
`tests/test_order_picker_script_js.py` и в настоящем Chrome в
`tests/test_order_picker_visibility.py`.
"""

from html.parser import HTMLParser
from pathlib import Path

from app.modules.auth.models import Role, User
from app.modules.auth.roles_router import PERMISSION_LABELS
from app.modules.auth.service import hash_password, login

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "password123"

CREATE = PERMISSION_LABELS["products.edit"]
PRICE = PERMISSION_LABELS["price.change"]


def _admin(client, db_session):
    from app.modules.auth.service import get_role_by_name

    u = User(username="boss", password_hash=hash_password(PASSWORD),
             role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(u)
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _user(client, db_session, username, permissions):
    role = Role(name=f"role-{username}", is_system=False, is_super=False,
                permissions=list(permissions))
    db_session.add(role)
    db_session.commit()
    db_session.add(User(username=username, password_hash=hash_password(PASSWORD),
                        role_id=role.id))
    db_session.commit()
    client.cookies.set("session", login(db_session, username, PASSWORD).token)


class _Lines(HTMLParser):
    """Кнопки внутри блоков подстановки: чей это блок (товар или клиент) и
    погашена ли кнопка."""

    def __init__(self):
        super().__init__()
        self.kind = None
        self.buttons = []
        self._stack = []
        self._reading = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            self._stack.append(a)
            if "app-picker" in a.get("class", ""):
                self.kind = a.get("data-lookup")
        elif tag == "button" and self.kind:
            self._reading = {"kind": self.kind, "disabled": "disabled" in a,
                             "attrs": a, "text": ""}
            self.buttons.append(self._reading)

    def handle_data(self, data):
        if self._reading is not None:
            self._reading["text"] += data.strip()

    def handle_endtag(self, tag):
        if tag == "button":
            self._reading = None
        elif tag == "div" and self._stack:
            closed = self._stack.pop()
            if "app-picker" in closed.get("class", ""):
                self.kind = None


def _buttons(body):
    parser = _Lines()
    parser.feed(body)
    return parser.buttons


def _button(body, label):
    found = [b for b in _buttons(body) if b["text"] == label]
    assert found, f"в форме заказа нет кнопки «{label}»: {[b['text'] for b in _buttons(body)]}"
    return found[0]


ORDER_RIGHTS = ["orders.view", "orders.create", "products.view"]


def test_the_product_line_offers_both_buttons(client, db_session):
    _admin(client, db_session)
    body = client.get("/orders/new").text
    for label in ("Создать товар", "Выбрать товар"):
        assert _button(body, label)["kind"] == "/api/lookup/products", (
            f"кнопка «{label}» оказалась не у товара")


def test_the_customer_line_has_neither_of_them(client, db_session):
    """Клиента нельзя создать из заказа, а кнопка на его строке ввела бы в
    заблуждение: она открыла бы окно товаров."""
    _admin(client, db_session)
    body = client.get("/orders/new").text
    assert [b["text"] for b in _buttons(body) if b["kind"] == "/api/lookup/customers"] == []


def test_a_user_with_the_right_gets_a_working_button_and_no_notice(client, db_session):
    _admin(client, db_session)
    body = client.get("/orders/new").text
    assert _button(body, "Создать товар")["disabled"] is False
    assert "Создание товара недоступно" not in body


def test_without_the_right_the_notice_names_the_permission_in_russian(client, db_session):
    _user(client, db_session, "seller", ORDER_RIGHTS)
    body = client.get("/orders/new").text
    assert CREATE in body, "уведомление обязано называть право словами, а не кодом"
    assert "products.edit" not in body, "человек не может действовать на код"
    assert _button(body, "Создать товар")["disabled"] is True


def test_the_notice_names_every_permission_that_is_missing(client, db_session):
    """Цена в форме есть, а `POST /api/products` требует `price.change` вместе с
    `products.edit`. Уведомление, молчащее про цену, обмануло бы человека: он
    прочитал «у меня есть права на товары» и упёрся в отказ."""
    _user(client, db_session, "seller", ORDER_RIGHTS)
    body = client.get("/orders/new").text
    assert PRICE in body, PRICE


def test_the_notice_names_only_what_is_missing(client, db_session):
    _user(client, db_session, "seller", ORDER_RIGHTS + ["products.edit"])
    body = client.get("/orders/new").text
    assert PRICE in body
    assert CREATE not in body, "это право есть, называть его незачем"


def test_the_gone_button_is_backed_by_the_api_refusing(client, db_session):
    """`disabled` — это картинка. Если эндпоинт продолжит создавать товар без
    права, то гашение кнопки ничего не защищает."""
    _user(client, db_session, "seller", ORDER_RIGHTS + ["price.change"])
    assert client.post("/api/products",
                       json={"name": "Гвоздь", "price": 10.0}).status_code == 403


class _Notices(HTMLParser):
    """Абзацы-сообщения страницы вместе с их классами."""

    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self._reading = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._reading = {"class": dict(attrs).get("class", ""), "text": ""}
            self.paragraphs.append(self._reading)

    def handle_data(self, data):
        if self._reading is not None:
            self._reading["text"] += data

    def handle_endtag(self, tag):
        if tag == "p":
            self._reading = None


def test_the_notice_looks_like_the_other_banners_of_the_page(client, db_session):
    """Уведомление обязано быть той же плашкой, что и остальные сообщения
    страницы, а не новым видом: человек знает, куда смотреть. Плашка отказа в
    этом шаблоне одна — та же строка классов, что у сообщения `?msg=`."""
    _user(client, db_session, "seller", ORDER_RIGHTS)
    parser = _Notices()
    parser.feed(client.get("/orders/new").text)
    notices = [p for p in parser.paragraphs if "Создание товара недоступно" in p["text"]]
    assert len(notices) == 1, [p["text"] for p in parser.paragraphs]
    assert notices[0]["class"] == "mb-4 bg-danger-bg border border-danger-border p-2", (
        f"уведомление нарисовано не так, как остальные сообщения: {notices[0]['class']}")


def test_the_permission_label_is_taken_from_the_map_and_not_written_by_hand():
    """Подпись обязана приходить из `PERMISSION_LABELS`. Захардкоженная в шаблоне
    пережила бы переименование права в карточке роли: страница заказа продолжила
    бы называть старое имя."""
    form = (ROOT / "app" / "templates" / "order_form.html").read_text(encoding="utf-8")
    for label in (CREATE, PRICE):
        assert label not in form, f"подпись права «{label}» написана руками в шаблоне"
    assert "products.edit" not in form and "price.change" not in form