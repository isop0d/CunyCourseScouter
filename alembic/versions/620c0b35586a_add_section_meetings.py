"""add section_meetings

Revision ID: 620c0b35586a
Revises: c66ed2859d24
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '620c0b35586a'
down_revision: Union[str, Sequence[str], None] = 'c66ed2859d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'section_meetings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('section_id', sa.Integer(), nullable=False),
        sa.Column('day_of_week', sa.SmallInteger(), nullable=False),
        sa.Column('start_minute', sa.Integer(), nullable=False),
        sa.Column('end_minute', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['section_id'], ['sections.class_number'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_meetings_section', 'section_meetings', ['section_id'])

    # Backfill from existing sections.days_times
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT class_number, days_times FROM sections")).fetchall()

    from cuny_scouter.meetings import parse_meetings
    insert_sql = sa.text(
        "INSERT INTO section_meetings (section_id, day_of_week, start_minute, end_minute) "
        "VALUES (:sid, :dow, :start, :end)"
    )
    for class_number, days_times in rows:
        for m in parse_meetings(days_times or ""):
            conn.execute(insert_sql, {
                "sid": class_number,
                "dow": m.day_of_week,
                "start": m.start_minute,
                "end": m.end_minute,
            })


def downgrade() -> None:
    op.drop_index('idx_meetings_section', table_name='section_meetings')
    op.drop_table('section_meetings')
