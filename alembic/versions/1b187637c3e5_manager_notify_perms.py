"""manager notify perms

Revision ID: 1b187637c3e5
Revises: 56b027ed71e9
Create Date: 2026-09-22 22:32:01.021765

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '1b187637c3e5'
down_revision: Union[str, Sequence[str], None] = '56b027ed71e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, permissions FROM roles WHERE name = 'manager'")).fetchall()
    for role_id, perms in rows:
        current = list(perms or [])
        for p in ("notifications.send", "notifications.view"):
            if p not in current:
                current.append(p)
        conn.execute(
            text("UPDATE roles SET permissions = :perms WHERE id = :id"),
            {"perms": json.dumps(sorted(current), ensure_ascii=False), "id": role_id},
        )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, permissions FROM roles WHERE name = 'manager'")).fetchall()
    for role_id, perms in rows:
        current = [p for p in (perms or []) if p not in ("notifications.send", "notifications.view")]
        conn.execute(
            text("UPDATE roles SET permissions = :perms WHERE id = :id"),
            {"perms": json.dumps(sorted(current), ensure_ascii=False), "id": role_id},
        )
