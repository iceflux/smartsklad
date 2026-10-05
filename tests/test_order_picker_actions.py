"""Две кнопки у строки заказа: «Создать товар» и «Выбрать товар».

Обе живут в блоке подстановки товара, а не на странице товаров: опечатку в
названии человек правит, не закрывая заказ, а выбрать из тысяч строк можно
просмотром, а не набором.

Про права — отдельно, потому что это требование владельца словами: если права
нет, человек должен узнать, КАКОГО именно права ему не хватает, и по-человечески.
Поэтому подпись берётся из `PERMISSION_LABELS` (человек не может действовать на
код `products.edit`), и проверка запрещает и код, и захардкоженную в шаблоне
подпись.

Кнопка не прячется, а остаётся на месте и выглядит недоступной, и вот почему:
владелец просил уведомление по нажатию, а у спрятанной кнопки уведомление
недостижимо. И это не `disabled`, а `aria-disabled`: настоящий `disabled` не
получает события click и не берётся с клавиатуры, то есть «оставить гашеной и
объяснить по клику» невозможно: нажать не на что. Право выдаётся на
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
    доступна ли кнопка."""

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
                             "ariaDisabled": a.get("aria-disabled"),
                             "notice": a.get("data-notice"), "attrs": a, "text": ""}
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


def test_the_customer_line_offers_its_own_buttons(client, db_session):
    """У клиента свои две кнопки — свои окна. Кнопка товара на строке клиента
    открыла бы окно товаров, и это заметил бы только тот, кто нажал."""
    _admin(client, db_session)
    body = client.get("/orders/new").text
    found = {b["text"]: b for b in _buttons(body)
             if b["kind"] == "/api/lookup/customers"}
    assert sorted(found) == ["Выбрать клиента", "Создать клиента"], sorted(found)
    assert found["Выбрать клиента"]["attrs"]["data-picker-browse"] == "customers"
    assert found["Создать клиента"]["attrs"]["data-picker-create"] == "customers"


def test_the_customer_buttons_are_hidden_without_the_right(client, db_session):
    """Без `customers.manage` подстановка клиента всё равно отвечает 403, поэтому
    кнопка окна открыла бы пустое окно: её у человека нет."""
    _user(client, db_session, "picker", ["orders.view", "orders.create",
                                          "products.view", "products.edit",
                                          "price.change"])
    body = client.get("/orders/new").text
    assert _buttons(body) == [b for b in _buttons(body)
                               if b["kind"] == "/api/lookup/products"], (
        "кнопки клиента показаны тому, у кого нет права на клиентов")


def test_a_user_with_the_right_gets_a_working_button_and_no_notice(client, db_session):
    _admin(client, db_session)
    body = client.get("/orders/new").text
    assert _button(body, "Создать товар")["ariaDisabled"] == "false"
    assert "Создание товара недоступно" not in body


def test_without_the_right_the_notice_names_the_permission_in_russian(client, db_session):
    _user(client, db_session, "seller", ORDER_RIGHTS)
    button = _button(client.get("/orders/new").text, "Создать товар")
    assert CREATE in button["notice"], (
        "уведомление обязано называть право словами, а не кодом")
    assert "products.edit" not in client.get("/orders/new").text, \
        "человек не может действовать на код"


def test_the_unavailable_button_is_never_a_real_disabled_one(client, db_session):
    """`disabled` не получает события click и не берётся с клавиатуры. Кнопка,
    которая должна объяснять недостающее право по нажатию, обязана оставаться
    доступной: настоящий `disabled` сделал бы уведомление недостижимым, и
    человек с клавиатуры (и с программой чтения) не узнал бы, что функция есть."""
    _user(client, db_session, "seller", ORDER_RIGHTS)
    button = _button(client.get("/orders/new").text, "Создать товар")
    assert button["ariaDisabled"] == "true"
    assert button["disabled"] is False, (
        "кнопка снова настоящий disabled: по ней нельзя нажать и до неё "
        "не дойти с клавиатуры")


def test_the_banner_appears_on_a_click_and_not_before_it(client, db_session):
    """Владелец попросил уведомление по нажатию, а не всегда: человек с правом не
    должен платить за чужое отсутствие права пустой рамкой на странице."""
    _user(client, db_session, "seller", ORDER_RIGHTS)
    body = client.get("/orders/new").text
    assert "Создание товара недоступно: нет права" not in body, (
        "плашка о недостающем праве снова висит на странице до нажатия")
    assert 'data-picker-notice' in body, "в форме нет места, куда показывать плашку"


def test_the_notice_names_every_permission_that_is_missing(client, db_session):
    """Цена в форме есть, а `POST /api/products` требует `price.change` вместе с
    `products.edit`. Уведомление, молчащее про цену, обмануло бы человека: он
    прочитал «у меня есть права на товары» и упёрся в отказ."""
    _user(client, db_session, "seller", ORDER_RIGHTS)
    assert PRICE in _button(client.get("/orders/new").text, "Создать товар")["notice"]


def test_the_notice_names_only_what_is_missing(client, db_session):
    _user(client, db_session, "seller", ORDER_RIGHTS + ["products.edit"])
    notice = _button(client.get("/orders/new").text, "Создать товар")["notice"]
    assert PRICE in notice
    assert CREATE not in notice, "это право есть, называть его незачем"


def test_the_gone_button_is_backed_by_the_api_refusing(client, db_session):
    """Гашение кнопки — это картинка. Если эндпоинт продолжит создавать товар без
    права, то ни `aria-disabled`, ни плашка ничего не защитят: человек прочёл бы
    объяснение и всё равно получил бы товар."""
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
            attrs = dict(attrs)
            self._reading = {"class": attrs.get("class", ""), "attrs": attrs, "text": ""}
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
    parser.feed(client.get("/orders/new?msg=Товар создан").text)
    banners = [p for p in parser.paragraphs
               if p["class"] == "mb-4 bg-danger-bg border border-danger-border p-2"
               and p["text"] == "Товар создан"]
    assert len(banners) == 1, [p["class"] for p in parser.paragraphs]
    placeholders = [p for p in parser.paragraphs if "data-picker-notice" in p["attrs"]]
    assert len(placeholders) == 1, "в форме нет подписи уведомления о правах"
    assert placeholders[0]["class"] == (
        "app-notice mb-4 bg-danger-bg border border-danger-border p-2"), (
        f"уведомление нарисовано не так, как остальные сообщения: "
        f"{placeholders[0]['class']}")
    assert banners, "на странице больше нет ни одного сообщения с классом плашки"


def test_the_empty_banner_takes_no_room_when_there_is_nothing_to_say():
    """Плашка появляется по нажатию, то есть большую часть времени пуста. Без
    правила `:empty` на странице, где право есть, осталась бы рамка без текста —
    и это выглядело бы поломкой, а не отсутствием сообщения."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text(
        encoding="utf-8")
    rules = re.findall(r"\.app-notice:empty\{([^}]*)\}", css)
    assert rules, "правило .app-notice:empty не доехало до сборки"
    assert any(body.strip() for body in rules), "правило .app-notice:empty пустое"
    assert "display:none" in rules[0].replace(" ", ""), rules
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "tailwind.css"
              ).read_text(encoding="utf-8")
    assert re.search(r"\.app-notice:empty\s*\{[^}]+\}", source), (
        "в исходнике нет правила — сборка его вычистит молча")


def test_the_unavailable_button_looks_unavailable(client, db_session):
    """Кнопка нажимается, поэтому недоступность обязана быть видна: без
    приглушения человек кликает по кнопке, которая выглядит рабочей. Правило
    проверяется и в исходнике, и в артефакте: сборщик вычищает `@layer components`
    по вхождению класса, и вычеркнутое из исходника правило до артефакта не
    доедет, а проверка только артефакта такой правки бы не заметила."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    css = (root / "app" / "static" / "app.css").read_text(encoding="utf-8")
    source = (root / "app" / "static" / "tailwind.css").read_text(encoding="utf-8")
    for where, text in (("артефакт", css), ("исходник", source)):
        rules = re.findall(r'\[aria-disabled="?true"?\]\s*\{([^}]*)\}', text)
        assert rules, f"недоступная кнопка ничем не отличается от доступной ({where})"
        body = "".join(rules)
        assert "opacity" in body, f"{where}: {rules}"
        assert "cursor:not-allowed" in body.replace(" ", ""), f"{where}: {rules}"


def test_the_permission_label_is_taken_from_the_map_and_not_written_by_hand():
    """Подпись обязана приходить из `PERMISSION_LABELS`. Захардкоженная в шаблоне
    пережила бы переименование права в карточке роли: страница заказа продолжила
    бы называть старое имя."""
    form = (ROOT / "app" / "templates" / "order_form.html").read_text(encoding="utf-8")
    for label in (CREATE, PRICE):
        assert label not in form, f"подпись права «{label}» написана руками в шаблоне"
    assert "products.edit" not in form and "price.change" not in form