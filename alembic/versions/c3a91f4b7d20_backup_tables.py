"""backup tables

Revision ID: c3a91f4b7d20
Revises: 7c1d2e3f4a5b
Create Date: 2026-09-25 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3a91f4b7d20'
down_revision: Union[str, Sequence[str], None] = '7c1d2e3f4a5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'backup_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('schedule_time', sa.Time(), nullable=False),
        sa.Column('check_interval_minutes', sa.Integer(), nullable=False),
        sa.Column('local_path', sa.String(length=512), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('pg_dump_path', sa.String(length=512), nullable=True),
        sa.Column('last_check_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_backup_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status', sa.String(length=16), nullable=True),
        sa.Column('last_error', sa.String(length=512), nullable=True),
        sa.Column('last_report_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_report_sent', sa.Integer(), nullable=False),
        sa.Column('last_report_skipped', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'backup_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('filename', sa.String(length=256), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error', sa.String(length=512), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('backup_log')
    op.drop_table('backup_settings')
