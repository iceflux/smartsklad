"""add description to products

Revision ID: 3c9d5e17a204
Revises: 8e2f1a9c4b71
Create Date: 2026-10-03 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c9d5e17a204'
down_revision: Union[str, Sequence[str], None] = '8e2f1a9c4b71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('products', sa.Column('description', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'description')