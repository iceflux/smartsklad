"""import map

Revision ID: 5d8c1e4a7b90
Revises: c3a91f4b7d20
Create Date: 2026-09-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5d8c1e4a7b90'
down_revision: Union[str, Sequence[str], None] = 'c3a91f4b7d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'import_map',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(length=16), nullable=False),
        sa.Column('source_id', sa.String(length=64), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('source_label', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entity_type', 'source_id', name='uq_import_map'),
    )


def downgrade() -> None:
    op.drop_table('import_map')
