"""units

Revision ID: 8e2f1a9c4b71
Revises: 5d8c1e4a7b90
Create Date: 2026-09-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '8e2f1a9c4b71'
down_revision: Union[str, Sequence[str], None] = '5d8c1e4a7b90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_UNITS = (
    "шт", "кг", "г", "т", "л", "мл", "м", "м2", "м3", "см", "мм",
    "упак", "ящ", "короб", "пач", "пакет", "бут", "бан", "вед", "пал",
    "пара", "компл", "набор", "рул", "ед", "ч", "мин", "сут", "дюжина",
    "лист", "кат", "дюйм", "фут", "галл",
)


def upgrade() -> None:
    op.create_table(
        'units',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=32), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    bind = op.get_bind()
    for name in DEFAULT_UNITS:
        bind.execute(
            text("INSERT INTO units (name, is_deleted) VALUES (:name, false)"),
            {"name": name},
        )
    op.add_column('products', sa.Column('unit_id', sa.Integer(), nullable=True))
    bind.execute(text("""
        INSERT INTO units (name, is_deleted)
        SELECT DISTINCT ON (lower(btrim(products.unit))) btrim(products.unit), false FROM products
        WHERE products.unit IS NOT NULL AND btrim(products.unit) <> ''
          AND lower(btrim(products.unit)) NOT IN (SELECT lower(name) FROM units)
        ORDER BY lower(btrim(products.unit)), btrim(products.unit)
    """))
    bind.execute(text("""
        UPDATE products SET unit_id = units.id FROM units
        WHERE lower(units.name) = lower(coalesce(nullif(btrim(products.unit), ''), 'шт'))
    """))
    op.alter_column('products', 'unit_id', nullable=False)
    op.create_foreign_key('fk_products_unit_id', 'products', 'units', ['unit_id'], ['id'])
    op.drop_column('products', 'unit')


def downgrade() -> None:
    op.add_column('products', sa.Column('unit', sa.String(length=32), server_default='шт', nullable=False))
    bind = op.get_bind()
    bind.execute(text("""
        UPDATE products SET unit = units.name FROM units WHERE products.unit_id = units.id
    """))
    op.drop_constraint('fk_products_unit_id', 'products', type_='foreignkey')
    op.drop_column('products', 'unit_id')
    op.drop_table('units')
