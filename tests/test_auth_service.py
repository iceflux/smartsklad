from app.config import settings
from app.modules.auth.models import User
from app.modules.auth.service import (
    create_admin_if_empty,
    create_session,
    get_role_by_name,
    get_user_by_token,
    hash_password,
    login,
    logout,
    verify_password,
)


def test_hash_and_verify():
    h = hash_password("password123")
    assert h != "password123"
    assert verify_password("password123", h)
    assert not verify_password("wrong", h)


def test_login_wrong_user_returns_none(db_session):
    assert login(db_session, "nobody", "x") is None


def test_login_logout_flow(db_session):
    u = User(username="seller1", password_hash=hash_password("password123"), role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()

    s = login(db_session, "seller1", "password123")
    assert s is not None
    got = get_user_by_token(db_session, s.token)
    assert got is not None and got.username == "seller1"

    logout(db_session, s.token)
    assert get_user_by_token(db_session, s.token) is None