from app.modules.audit.models import AuditLog
from app.modules.audit.service import log_action


def test_log_action_writes_row(db_session):
    log_action(
        db_session,
        action="create",
        entity_type="product",
        entity_id=1,
        old_value=None,
        new_value='{"name": "Кирпич"}',
        user_id=1,
        ip="127.0.0.1",
    )

    row = db_session.query(AuditLog).one()
    assert row.action == "create"
    assert row.entity_type == "product"
    assert row.entity_id == 1
    assert row.new_value == '{"name": "Кирпич"}'
    assert row.user_id == 1
    assert row.ip == "127.0.0.1"