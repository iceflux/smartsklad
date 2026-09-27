from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password, login
from app.modules.inventory.service import (
    create_product,
    get_product,
    get_unit_by_name,
    update_product,
)
from app.modules.settings.service import STOCK_TRACK_DEFAULT_KEY, set_setting

PASSWORD = "password123"


def _admin(client, db_session):
    u = db_session.query(User).filter(User.username == "boss").first()
    if u is None:
        u = User(username="boss", password_hash=hash_password(PASSWORD),
                 role_id=get_role_by_name(db_session, "admin").id)
        db_session.add(u)
        db_session.commit()
    s = login(db_session, "boss", PASSWORD)
    client.cookies.set("session", s.token)


def _unit_id(db: Session) -> int:
    return get_unit_by_name(db, "шт").id


def _mk(db: Session, **kw):
    return create_product(db, name=kw.get("name", "Тест"), sku=kw.get("sku", "SKU-1"),
                          category_id=None, unit_id=_unit_id(db), price=10.0, characteristic=None,
                          rest=5, **{k: v for k, v in kw.items() if k in ("track_rest",)})


def test_create_defaults_from_global(db_session):
    p = _mk(db_session)
    assert p.track_rest is True  # дефолт глобалки = true


def test_global_setting_drives_default(db_session):
    set_setting(db_session, STOCK_TRACK_DEFAULT_KEY, "false", user=None)
    p = _mk(db_session, name="Сервис", sku="SKU-GLB")
    assert p.track_rest is False


def test_explicit_true_beats_global_false(db_session):
    set_setting(db_session, STOCK_TRACK_DEFAULT_KEY, "false", user=None)
    p = _mk(db_session, name="Сервис", sku="SKU-EXPL", track_rest=True)
    assert p.track_rest is True


def test_create_explicit_false(db_session):
    p = _mk(db_session, track_rest=False)
    assert p.track_rest is False


def test_create_explicit_true(db_session):
    p = _mk(db_session, track_rest=True)
    assert p.track_rest is True


def test_update_track_rest(db_session):
    p = _mk(db_session, track_rest=False)
    update_product(db_session, p.id, {"track_rest": True})
    assert get_product(db_session, p.id).track_rest is True


def test_product_out_contains_flag(client, db_session):
    _admin(client, db_session)
    r = client.post("/api/products", json={
        "name": "T", "sku": "SKU-X", "price": 1.0, "rest": 0, "track_rest": False,
    })
    assert r.status_code < 500
    body = r.json()
    assert "track_rest" in body
    assert body["track_rest"] is False
