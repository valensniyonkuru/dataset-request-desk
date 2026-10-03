from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analytics import compute_analytics
from app.api_docs import NOT_LOGGED_IN, error_responses
from app.auth import require_roles
from app.db import get_db
from app.models import User
from app.schemas import AnalyticsOut

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_DAYS = 366  # one year, including a leap year

DESCRIPTION = f"""
**Roles:** operator, admin. All dates are UTC days, and both `from` and `to` are included:
the range is from 00:00 on `from` up to (not including) 00:00 on the day after `to`.
At most {MAX_DAYS} days.

- **episodes_per_day**: episodes recorded per UTC day and robot. Sparse: days and robots
  without episodes are left out.
- **requests**: requests **created** in the range. `by_status` is their current status
  (all five keys, zero when none). `delivered_count` is how many of them were delivered
  at least once. `median_hours_submitted_to_delivered` is the median time from creation
  (the first `submitted` history row) to the **first** `delivered` history row, in hours
  with 2 decimals; a rework loop does not reset the clock. Null when none was delivered.
- **top_tasks**: the 5 task names with the most `good` episodes recorded in the range,
  most first, ties by name.

**422**: `from` or `to` missing or not a date (YYYY-MM-DD), `from` after `to`, or more than {MAX_DAYS} days.
"""


@router.get(
    "",
    response_model=AnalyticsOut,
    summary="Episodes per day, request fulfilment and top tasks",
    description=DESCRIPTION,
    responses=error_responses({401: NOT_LOGGED_IN, 403: "Clients cannot see analytics."}),
)
def get_analytics(
    from_date: date = Query(alias="from", description="First day, included (YYYY-MM-DD, UTC)."),
    to_date: date = Query(alias="to", description="Last day, included (YYYY-MM-DD, UTC)."),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles("operator", "admin")),
):
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="from must not be after to")
    days = (to_date - from_date).days + 1
    if days > MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"The range is {days} days; at most {MAX_DAYS} are allowed")

    # Half-open range: >= start of `from`, < start of the day after `to`. No
    # "23:59:59.999" edge cases, and the WHERE clause can use the index.
    start = datetime.combine(from_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone.utc)

    result = compute_analytics(db, start, end)
    result["meta"] = {
        "from_date": from_date,
        "to_date": to_date,
        "days": days,
        "generated_at": datetime.now(timezone.utc),
    }
    return result
