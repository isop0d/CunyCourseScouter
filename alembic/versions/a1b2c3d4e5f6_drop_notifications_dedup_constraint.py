"""drop notifications dedup unique constraint

Revision ID: a1b2c3d4e5f6
Revises: 5660028e5c29
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '972663c1a9f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('uq_notifications_dedup', 'notifications', type_='unique')


def downgrade() -> None:
    op.create_unique_constraint(
        'uq_notifications_dedup',
        'notifications',
        ['watch_id', 'from_status', 'to_status'],
    )
