"""statistics for the analytics day buckets

Revision ID: 8802d98bd10a
Revises: f95cc18aecf7
Create Date: 2026-10-03 16:02:46.996397

The analytics query groups episodes by UTC day and robot. Postgres keeps no
statistics on the expression (recorded_at AT TIME ZONE 'UTC')::date, so it
guessed about 140 000 groups where there were 150, chose to sort every row and
spilled the sort to disk. With these statistics it estimates the groups
correctly and aggregates in memory. Measured on 2 000 000 episodes: 296 -> 175 ms
for 30 days, 631 -> 284 ms for 365 days (docs/analytics-scale.md).

This is not an index: nothing extra is written when episodes are inserted.
ANALYZE fills it in (autovacuum runs ANALYZE after large changes, e.g. an import).
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8802d98bd10a"
down_revision: Union[str, Sequence[str], None] = "f95cc18aecf7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The expression must be written exactly as in app/analytics.py for the planner to use it.
    op.execute(
        "CREATE STATISTICS st_episodes_utc_day_robot (ndistinct) "
        "ON ((recorded_at AT TIME ZONE 'UTC')::date), robot_id FROM episodes"
    )
    op.execute("ANALYZE episodes")  # fill the statistics now, not at the next autovacuum


def downgrade() -> None:
    op.execute("DROP STATISTICS st_episodes_utc_day_robot")
