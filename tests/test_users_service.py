import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from app.modules.auth.service import (
    UserConflictError,
    UserPermissionError,
    create_user,
    generate_password,
    get_role_by_name,
    hash_password,
    update_user,
)

PASSWORD = "password123"


def _admin(db_session):
    u = User(
        username="boss",
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, "admin").id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _seller(db_session):
    u = User(
        username="seller1",
        password_hash=hash_password(PASSWORD),
        role_id=get_role_by_name(db_session, "seller").id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _users_manager(db_session, username="cmanager"):
    role = Role(name=username, is_system=False, is_super=False, permissions=["users.manage"])
    db_session.add(role)
    db_session.commit()
    u = User(username=username, password_hash=hash_password(PASSWORD), role_id=role.id)
    db_session.add(u)
    db_session.commit()
    return u


def test_generate_password_length_and_charset():
    charset = "ABC123"
    p = generate_password(charset, 12)
    assert len(p) == 12
    assert all(c in charset for c in p)


def test_generate_password_varies():
    p1 = generate_password("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", 16)
    p2 = generate_password("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", 16)
    assert p1 != p2


def test_create_user_success_and_audit(db_session):
    actor = _admin(db_session)
    role = get_role_by_name(db_session, "seller")
    u = create_user(
        db_session, username="newbie", email="new@x.ru",
        password="secret123", role_id=role.id, actor=actor,
    )
    assert u.username == "newbie"
    assert u.email == "new@x.ru"
    assert u.role_id == role.id
    entries = db_session.query(AuditLog).filter_by(action="user.create", entity_id=u.id).all()
    assert len(entries) == 1
    assert entries[0].new_value == "newbie"
    assert entries[0].user_id == actor.id


def test_create_user_preserves_inactive_status(db_session):
    actor = _admin(db_session)
    role = get_role_by_name(db_session, "seller")
    user = create_user(
        db_session, username="inactive", email=None, password="secret123",
        role_id=role.id, actor=actor, is_active=False,
    )
    assert user.is_active is False


def test_create_user_duplicate_username(db_session):
    actor = _admin(db_session)
    role = get_role_by_name(db_session, "seller")
    create_user(db_session, username="dup", email=None, password="secret123",
                role_id=role.id, actor=actor)
    with pytest.raises(UserConflictError):
        create_user(db_session, username="dup", email=None, password="secret123",
                    role_id=role.id, actor=actor)


def test_create_user_duplicate_email(db_session):
    actor = _admin(db_session)
    role = get_role_by_name(db_session, "seller")
    create_user(db_session, username="u1", email="same@x.ru", password="secret123",
                role_id=role.id, actor=actor)
    with pytest.raises(UserConflictError):
        create_user(db_session, username="u2", email="same@x.ru", password="secret123",
                    role_id=role.id, actor=actor)


def test_create_user_short_password(db_session):
    actor = _admin(db_session)
    role = get_role_by_name(db_session, "seller")
    with pytest.raises(ValueError):
        create_user(db_session, username="u3", email=None, password="short",
                    role_id=role.id, actor=actor)


def test_create_user_empty_username(db_session):
    actor = _admin(db_session)
    role = get_role_by_name(db_session, "seller")
    with pytest.raises(ValueError):
        create_user(db_session, username="   ", email=None, password="secret123",
                    role_id=role.id, actor=actor)


def test_create_user_role_not_found(db_session):
    actor = _admin(db_session)
    with pytest.raises(ValueError):
        create_user(db_session, username="u4", email=None, password="secret123",
                    role_id=99999, actor=actor)


def test_create_user_non_super_cannot_grant_super_role(db_session):
    actor = _users_manager(db_session)
    admin_role_id = get_role_by_name(db_session, "admin").id
    with pytest.raises(UserPermissionError):
        create_user(db_session, username="u5", email=None, password="secret123",
                    role_id=admin_role_id, actor=actor)


def test_update_user_changes_fields(db_session):
    actor = _admin(db_session)
    u = _seller(db_session)
    role = get_role_by_name(db_session, "manager")
    updated = update_user(db_session, u, actor=actor, username="sellerX",
                          email="x@y.ru", role_id=role.id, password="newpass123",
                          is_active=False)
    assert updated.username == "sellerX"
    assert updated.email == "x@y.ru"
    assert updated.role_id == role.id
    assert updated.is_active is False
    entries = db_session.query(AuditLog).filter_by(entity_id=u.id).all()
    actions = [e.action for e in entries]
    assert "user.update" in actions
    assert "user.role.change" in actions
    assert "user.status_change" in actions
    assert any("newpass123" not in (e.new_value or "") for e in entries)


def test_update_user_duplicate_username(db_session):
    actor = _admin(db_session)
    _seller(db_session)
    u2 = User(username="seller2", password_hash=hash_password(PASSWORD),
              role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u2)
    db_session.commit()
    with pytest.raises(UserConflictError):
        update_user(db_session, u2, actor=actor, username="seller1")


def test_update_user_short_password_raises(db_session):
    actor = _admin(db_session)
    u = _seller(db_session)
    with pytest.raises(ValueError):
        update_user(db_session, u, actor=actor, password="short")


def test_update_self_role_forbidden(db_session):
    actor = _users_manager(db_session)
    role = get_role_by_name(db_session, "seller")
    with pytest.raises(UserPermissionError):
        update_user(db_session, actor, actor=actor, role_id=role.id)


def test_deactivate_self_forbidden(db_session):
    actor = _users_manager(db_session)
    role = get_role_by_name(db_session, "seller")
    with pytest.raises(UserPermissionError):
        update_user(db_session, actor, actor=actor, role_id=role.id, is_active=False)


def test_deactivate_last_super_forbidden(db_session):
    actor = _users_manager(db_session)
    admin = _admin(db_session)
    with pytest.raises(UserPermissionError):
        update_user(db_session, admin, actor=actor, is_active=False)


def test_non_super_cannot_touch_super_user(db_session):
    actor = _users_manager(db_session)
    admin = _admin(db_session)
    with pytest.raises(UserPermissionError):
        update_user(db_session, admin, actor=actor, username="hacked")
    with pytest.raises(UserPermissionError):
        update_user(db_session, admin, actor=actor, password="newpass123")


def test_non_super_cannot_grant_super_role(db_session):
    actor = _users_manager(db_session)
    seller = _seller(db_session)
    admin_role_id = get_role_by_name(db_session, "admin").id
    with pytest.raises(UserPermissionError):
        update_user(db_session, seller, actor=actor, role_id=admin_role_id)


def test_super_can_deactivate_other_super_when_second_exists(db_session):
    actor = _admin(db_session)
    other = User(username="boss2", password_hash=hash_password(PASSWORD),
                 role_id=get_role_by_name(db_session, "admin").id)
    db_session.add(other)
    db_session.commit()
    updated = update_user(db_session, other, actor=actor, is_active=False)
    assert updated.is_active is False