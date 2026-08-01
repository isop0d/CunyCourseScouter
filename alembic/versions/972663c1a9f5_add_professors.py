"""add professors

Revision ID: 972663c1a9f5
Revises: 5660028e5c29
Create Date: 2026-08-01 00:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '972663c1a9f5'
down_revision: Union[str, Sequence[str], None] = '5660028e5c29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'professors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('normalized_name', sa.String(100), nullable=False),
        sa.Column('rmp_legacy_id', sa.Integer(), nullable=True),
        sa.Column('avg_rating', sa.Float(), nullable=True),
        sa.Column('avg_difficulty', sa.Float(), nullable=True),
        sa.Column('num_ratings', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('would_take_again', sa.Float(), nullable=True),
        sa.Column('match_status', sa.String(20), nullable=False),
        sa.Column('rmp_url', sa.String(200), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('normalized_name'),
    )
    op.create_index('ix_professors_normalized_name', 'professors', ['normalized_name'])


def downgrade() -> None:
    op.drop_index('ix_professors_normalized_name', table_name='professors')
    op.drop_table('professors')
