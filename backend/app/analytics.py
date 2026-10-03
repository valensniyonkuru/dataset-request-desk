"""Analytics: three SQL queries, one per section. All aggregation happens in Postgres.

Every query filters on a half-open UTC range, `column >= :start AND column < :end`.
The column is never wrapped in a function in WHERE, so its index can be used.
Days are UTC days whatever the database session's time zone: AT TIME ZONE 'UTC'.
The numbers on 200 000 and 2 000 000 episodes are in docs/analytics-scale.md.
"""

from datetime import datetime
from typing import get_args

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.workflow import Status

STATUSES = get_args(Status)

# Sparse: only (day, robot) pairs that have episodes.
EPISODES_PER_DAY = text("""
    SELECT (recorded_at AT TIME ZONE 'UTC')::date AS day, robot_id, count(*) AS episodes
    FROM episodes
    WHERE recorded_at >= :start AND recorded_at < :end
    GROUP BY day, robot_id
    ORDER BY day, robot_id
""")

# Requests created in the range. For each, the first "submitted" history row
# (written when it was created) and the first "delivered" one: a rework loop
# (delivered, rejected, delivered again) keeps the first delivery.
# percentile_cont ignores NULLs, so never-delivered requests are left out of the median.
REQUESTS_SUMMARY = text("""
    WITH scoped AS (
        SELECT id, status
        FROM requests
        WHERE created_at >= :start AND created_at < :end
    ),
    milestones AS (
        SELECT history.request_id,
               min(history.changed_at) FILTER (WHERE history.to_status = 'submitted') AS submitted_at,
               min(history.changed_at) FILTER (WHERE history.to_status = 'delivered') AS first_delivered_at
        FROM request_status_history AS history
        JOIN scoped ON scoped.id = history.request_id
        GROUP BY history.request_id
    )
    SELECT count(*) FILTER (WHERE scoped.status = 'submitted') AS submitted,
           count(*) FILTER (WHERE scoped.status = 'in_progress') AS in_progress,
           count(*) FILTER (WHERE scoped.status = 'delivered') AS delivered,
           count(*) FILTER (WHERE scoped.status = 'accepted') AS accepted,
           count(*) FILTER (WHERE scoped.status = 'rejected') AS rejected,
           count(*) AS total,
           count(milestones.first_delivered_at) AS delivered_count,
           round(
               extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY milestones.first_delivered_at - milestones.submitted_at
               )) / 3600,
               2
           ) AS median_hours
    FROM scoped
    LEFT JOIN milestones ON milestones.request_id = scoped.id
""")

TOP_TASKS = text("""
    SELECT task_name, count(*) AS good_episodes
    FROM episodes
    WHERE quality = 'good' AND recorded_at >= :start AND recorded_at < :end
    GROUP BY task_name
    ORDER BY good_episodes DESC, task_name
    LIMIT 5
""")


def compute_analytics(db: Session, start: datetime, end: datetime) -> dict:
    """The three sections for recorded_at / created_at in [start, end). Three queries, whatever the data size."""
    bounds = {"start": start, "end": end}

    per_day = db.execute(EPISODES_PER_DAY, bounds)
    summary = db.execute(REQUESTS_SUMMARY, bounds).mappings().one()
    top_tasks = db.execute(TOP_TASKS, bounds)

    return {
        "episodes_per_day": [{"date": day, "robot_id": robot, "count": count} for day, robot, count in per_day],
        "requests": {
            "by_status": {status: summary[status] for status in STATUSES},
            "total": summary["total"],
            "delivered_count": summary["delivered_count"],
            "median_hours_submitted_to_delivered": summary["median_hours"],
        },
        "top_tasks": [{"task_name": task, "good_episodes": count} for task, count in top_tasks],
    }
