"""add schedule_entries

Revision ID: 5660028e5c29
Revises: 620c0b35586a
Create Date: 2026-08-01 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5660028e5c29'
down_revision: Union[str, Sequence[str], None] = '620c0b35586a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'schedule_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('section_id', sa.Integer(), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['section_id'], ['sections.class_number']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('student_id', 'section_id'),
    )


def downgrade() -> None:
    op.drop_table('schedule_entries')
