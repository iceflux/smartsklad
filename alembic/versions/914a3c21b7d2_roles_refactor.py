"""roles refactor

Revision ID: 914a3c21b7d2
Revises: 65213176f82f
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '914a3c21b7d2'
down_revision: Union[str, Sequence[str], None] = '65213176f82f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'roles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('is_system', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_super', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_unique_constraint('uq_roles_name', 'roles', ['name'])

    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO roles (name, is_system, is_super, permissions) VALUES "
        "('admin', true, true, '[]'::json), "
        "('seller', true, false, '[\"products.view\", \"orders.view\", \"orders.create\", \"orders.edit\", \"orders.status\", \"customers.manage\"]'::json), "
        "('warehouseman', true, false, '[\"products.view\", \"stock.change\", \"orders.view\", \"orders.status\"]'::json), "
        "('manager', true, false, '[\"products.view\", \"price.change\", \"orders.view\", \"orders.create\", \"orders.edit\", \"orders.delete\", \"orders.status\", \"customers.manage\", \"analytics.view\", \"reports.export\"]'::json)"
    ))

    op.add_column('users', sa.Column('role_id', sa.Integer(), nullable=True))
    conn.execute(sa.text("UPDATE users u SET role_id = r.id FROM roles r WHERE r.name = u.role"))
    op.create_foreign_key('fk_users_role', 'users', 'roles', ['role_id'], ['id'])
    op.alter_column('users', 'role_id', nullable=False)
    op.drop_column('users', 'role')


def downgrade() -> None:
    op.add_column('users', sa.Column('role', sa.String(length=32), nullable=True))
    op.execute("UPDATE users u SET role = r.name FROM roles r WHERE r.id = u.role_id")
    op.alter_column('users', 'role', nullable=False)
    op.drop_constraint('fk_users_role', 'users', type_='foreignkey')
    op.drop_column('users', 'role_id')
    op.drop_table('roles')