from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.customers.models import Customer
from app.modules.inventory.models import Product
from app.modules.notifications.models import NotificationLog
from app.modules.orders.service import create_order
from app.modules.inventory.service import get_unit_by_name


def _unit_id(db_session):
    return get_unit_by_name(db_session, "шт").id


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


def _seller(client, db_session):
    _login(client, db_session, "seller1", "seller")


def _mk_order(db_session):
    p = Product(name="Гвозди", sku="NAIL-1", unit_id=_unit_id(db_session), price=100.0, rest=50)
    c = Customer(name="ООО Строй", email="a@b.ru")
    db_session.add_all([p, c])
    db_session.commit()
    return create_order(db_session, customer_id=c.id, items=[{"product_id": p.id, "quantity": 1, "discount": 0}])


def test_print_page_shows_send_buttons_for_admin(client, db_session):
    _admin(client, db_session)
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}/print")
    assert r.status_code == 200
    assert "На email" in r.text
    assert "В Telegram" in r.text


def test_print_page_hides_send_buttons_for_seller(client, db_session):
    _seller(client, db_session)
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}/print")
    assert r.status_code == 200
    assert "На email" not in r.text


def test_send_page_email_redirect(client, db_session, monkeypatch):
    _admin(client, db_session)
    o = _mk_order(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    r = client.post(f"/orders/{o.id}/send", data={"channel": "email"}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote_plus
    assert "Отправлено" in unquote_plus(r.headers["location"])
    assert db_session.query(NotificationLog).count() == 1


def test_history_page_shows_rows(client, db_session, monkeypatch):
    _admin(client, db_session)
    o = _mk_order(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    client.post(f"/orders/{o.id}/send", data={"channel": "email"})
    r = client.get("/notifications")
    assert r.status_code == 200
    assert "email" in r.text
    assert "a@b.ru" in r.text


def test_history_page_403_for_seller(client, db_session):
    _seller(client, db_session)
    r = client.get("/notifications")
    assert r.status_code == 403


def test_the_order_page_carries_every_document_control(client, db_session):
    """Заказ должен отдавать документ в один клик, а не в два.

    Предпросмотр печати остаётся отдельной ссылкой «Печать» — печать браузера
    всё равно требует страницы, где нет ни меню, ни кнопок, — но PDF и оба
    канала отправки живут прямо на заказе.
    """
    _admin(client, db_session)
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}")
    assert r.status_code == 200
    assert f'href="/orders/{o.id}/print"' in r.text, "нет ссылки на предпросмотр печати"
    assert f'href="/orders/{o.id}/pdf"' in r.text, "нет ссылки на скачивание PDF"
    assert "На email" in r.text, "нет отправки на email"
    assert "В Telegram" in r.text, "нет отправки в Telegram"


def test_the_order_page_hides_the_send_controls_for_a_seller(client, db_session):
    """Кнопка, которая ответит 403, хуже отсутствующей кнопки.

    `/orders/{id}/send` требует `notifications.send`, и право проверяется и в
    шаблоне (`can_send_notifications`), и в самом роутере. Без первого человек
    увидел бы кнопку и получил бы отказ.
    """
    _seller(client, db_session)
    o = _mk_order(db_session)
    r = client.get(f"/orders/{o.id}")
    assert r.status_code == 200
    assert f'href="/orders/{o.id}/pdf"' in r.text, "PDF читать seller имеет право"
    assert "На email" not in r.text, "кнопка отправки без права 403-ит"
    assert "В Telegram" not in r.text, "кнопка отправки без права 403-ит"
    assert client.post(f"/orders/{o.id}/send", data={"channel": "email"}).status_code == 403


def test_sending_from_the_order_page_returns_to_that_order(client, db_session, monkeypatch):
    """Отправка возвращает туда, откуда её нажали.

    Раньше единственной страницей с этими кнопками был предпросмотр печати, и
    роут возвращал именно туда. С появлением кнопок на самом заказе возврат на
    предпросмотр уводил бы человека со страницы, на которой он нажал, и
    сообщение об отправке он увидел бы не там, где ждал.
    """
    from urllib.parse import unquote_plus

    _admin(client, db_session)
    o = _mk_order(db_session)
    monkeypatch.setattr("app.config.settings.smtp_host", "localhost")
    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    r = client.post(f"/orders/{o.id}/send", data={"channel": "email"}, follow_redirects=False)
    assert r.status_code == 303
    location = unquote_plus(r.headers["location"])
    assert location.startswith(f"/orders/{o.id}?"), location
    assert "Отправлено" in location, location

    monkeypatch.setattr("app.modules.notifications.email.send_email", lambda *a, **k: None)
    r = client.post(f"/orders/{o.id}/send", data={"channel": "sms"}, follow_redirects=False)
    assert r.status_code == 303
    assert unquote_plus(r.headers["location"]).startswith(f"/orders/{o.id}?"), (
        "и отказ канала должен вернуть на заказ, а не на предпросмотр"
    )


def test_a_refused_channel_is_shown_on_the_order_page_as_a_refusal(client, db_session):
    """Отказ канала обязан быть виден и не должен выглядеть как успех.

    На странице заказа `msg` рисуется зелёной плашкой успеха, поэтому отказ
    приходит отдельным ключом `err` и рисуется красной плашкой. Раньше отказ не
    был виден вовсе: предпросмотр печати текст подсказки не выводит.
    """
    from urllib.parse import unquote_plus

    _admin(client, db_session)
    o = _mk_order(db_session)
    r = client.post(f"/orders/{o.id}/send", data={"channel": "carrier-pigeon"},
                    follow_redirects=False)
    assert r.status_code == 303
    location = unquote_plus(r.headers["location"])
    assert "err=" in location and "msg=" not in location, location

    page = client.get(location).text
    assert "bg-danger-bg" in page, "отказ должен быть в красной плашке"
    assert "Отправлено" not in page, "отказ не должен выглядеть как отправка"