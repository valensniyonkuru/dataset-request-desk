"""Request and response bodies.

Input and output models are separate. No output model has a password_hash
field, so a hash can never end up in a response, whatever a route returns.
"""

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.workflow import Status

Role = Literal["client", "operator", "admin"]
Quality = Literal["good", "usable", "bad"]

# Deliberately simple: one "@", no spaces, a dot in the domain. Good enough to
# catch typos in an internal tool, without an extra dependency.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ErrorOut(BaseModel):
    """Body of every error response except 422. Used only to document errors in /docs."""

    detail: str


class LoginIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        # A documented demo account (see README), so "Try it out" in /docs works as is.
        json_schema_extra={"examples": [{"email": "admin@example.com", "password": "admin123"}]},
    )

    # No email format check here: any wrong input simply gets the generic 401.
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    role: Role
    organisation: str | None


class UserAdminOut(UserOut):
    """What admins see in user management."""

    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "email": "new.client@example.com",
                    "name": "Gamma Robotics",
                    "role": "client",
                    "organisation": "Gamma Robotics",
                    "password": "choose-a-long-password",
                }
            ]
        },
    )

    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    role: Role
    organisation: str | None = Field(default=None, max_length=200)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value):
        # mode="before": runs before the pattern check. Emails are stored lowercase.
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def client_needs_organisation(self):
        if self.role == "client" and not self.organisation:
            raise ValueError("organisation is required for clients")
        return self


class UserUpdate(BaseModel):
    """Partial update: only the fields present in the request body are changed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    organisation: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def only_organisation_can_be_null(self):
        # The other columns are NOT NULL, so an explicit null for them is an error.
        for field in self.model_fields_set - {"organisation"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class RequestCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",  # e.g. a client_id in the body is rejected
        json_schema_extra={
            "examples": [
                {
                    "task_name": "pick cup",
                    "episodes_requested": 200,
                    "deadline": "2030-01-31",
                    "notes": "Robot arm, white cups on a table",
                }
            ]
        },
    )

    task_name: str = Field(min_length=1, max_length=100)
    # strict: only a JSON integer; "5", 5.0 and true are rejected.
    episodes_requested: int = Field(ge=1, le=100_000, strict=True)
    deadline: date
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("task_name", mode="before")
    @classmethod
    def trim_task_name(cls, value):
        # mode="before": trim first, so "   " fails the min_length check.
        return value.strip() if isinstance(value, str) else value

    @field_validator("deadline")
    @classmethod
    def deadline_not_in_the_past(cls, value: date) -> date:
        # Compared with today in UTC, the timezone the server runs in.
        if value < datetime.now(timezone.utc).date():
            raise ValueError("deadline cannot be in the past")
        return value


class TransitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"to_status": "in_progress"}]})

    to_status: Status


class RequestOut(BaseModel):
    id: int
    client_id: int
    client_name: str
    task_name: str
    episodes_requested: int
    deadline: date
    notes: str | None
    status: Status
    assigned_count: int
    created_at: datetime
    updated_at: datetime


class HistoryEntryOut(BaseModel):
    from_status: Status | None  # null on the row written when the request is created
    to_status: Status
    changed_by_name: str
    changed_at: datetime


class RequestDetailOut(RequestOut):
    history: list[HistoryEntryOut]  # oldest first
    # Status changes the current user may make right now, according to the workflow table.
    available_transitions: list[Status]


class EpisodeOut(BaseModel):
    episode_id: str
    robot_id: str
    task_name: str
    recorded_at: datetime
    duration_seconds: int
    operator_name: str
    quality: Quality
    import_run_id: int | None
    created_at: datetime


class EpisodeListItem(EpisodeOut):
    assigned_request_id: int | None  # null when the episode is not assigned


class EpisodePage(BaseModel):
    items: list[EpisodeListItem]
    total: int  # all rows matching the filters, not just this page
    limit: int
    offset: int


class AssignedEpisodeOut(EpisodeOut):
    assigned_at: datetime


class AssignedEpisodePage(BaseModel):
    items: list[AssignedEpisodeOut]
    total: int
    limit: int
    offset: int


class AssignIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"episode_ids": ["EP-00156", "EP-00138"]}]},
    )

    episode_ids: list[str] = Field(min_length=1, max_length=500)

    @field_validator("episode_ids")
    @classmethod
    def no_duplicates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("episode_ids contains the same id more than once")
        return value


class AssignOut(BaseModel):
    request_id: int
    assigned_count: int  # total for the request, after this batch
    assigned_episode_ids: list[str]  # the ids assigned by this call


class ImportProblem(BaseModel):
    line: int  # physical line in the file; the header is line 1
    episode_id: str | None
    reason: str
    value: str | None = None  # the offending input
    # For "conflict": {field: {"file": ..., "database": ...}} for each field that differs.
    fields: dict[str, dict[str, Any]] | None = None


class ImportReport(BaseModel):
    """What an import did. total_rows == imported + sum(skipped) + sum(rejected)."""

    status: Literal["running", "finished", "failed"]
    error: str | None = None  # only when status is "failed"
    file_name: str
    file_sha256: str
    total_rows: int  # data rows: not the header, not blank lines
    imported: int
    skipped: dict[str, int]
    rejected: dict[str, int]
    normalised: dict[str, int]
    blank_lines: int
    problems: list[ImportProblem]  # the first 200
    problems_truncated: bool
    previous_runs_with_same_file: list[int]
    duration_ms: int


class ImportResultOut(BaseModel):
    import_run_id: int
    report: ImportReport


class ImportRunSummaryOut(BaseModel):
    id: int
    file_name: str
    started_by_name: str | None  # null when started from the command line
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "finished", "failed"]
    total_rows: int | None
    imported: int | None
    skipped: int | None
    rejected: int | None


class ImportRunPage(BaseModel):
    items: list[ImportRunSummaryOut]
    total: int
    limit: int
    offset: int


class ImportRunOut(BaseModel):
    id: int
    file_name: str
    file_sha256: str
    started_by_name: str | None
    started_at: datetime
    finished_at: datetime | None
    report: ImportReport | None  # null while the import is still running


class EpisodesPerDayOut(BaseModel):
    date: date  # a UTC day
    robot_id: str
    count: int


class RequestFulfilmentOut(BaseModel):
    by_status: dict[Status, int]  # all five statuses, zero when none
    total: int
    delivered_count: int  # requests in scope delivered at least once
    median_hours_submitted_to_delivered: float | None  # null when none was delivered


class TopTaskOut(BaseModel):
    task_name: str
    good_episodes: int


class AnalyticsMetaOut(BaseModel):
    # "from" is a Python keyword, so the fields have other names and are renamed in the JSON.
    from_date: date = Field(serialization_alias="from")
    to_date: date = Field(serialization_alias="to")
    days: int
    generated_at: datetime


class AnalyticsOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "episodes_per_day": [
                        {"date": "2026-09-01", "robot_id": "arm-01", "count": 14},
                        {"date": "2026-09-01", "robot_id": "mobile-01", "count": 9},
                        {"date": "2026-09-02", "robot_id": "arm-01", "count": 11},
                    ],
                    "requests": {
                        "by_status": {"submitted": 2, "in_progress": 3, "delivered": 1, "accepted": 4, "rejected": 0},
                        "total": 10,
                        "delivered_count": 5,
                        "median_hours_submitted_to_delivered": 52.5,
                    },
                    "top_tasks": [
                        {"task_name": "pick cup", "good_episodes": 120},
                        {"task_name": "fold towel", "good_episodes": 97},
                    ],
                    "meta": {
                        "from": "2026-09-01",
                        "to": "2026-09-30",
                        "days": 30,
                        "generated_at": "2026-10-03T09:00:00Z",
                    },
                }
            ]
        }
    )

    episodes_per_day: list[EpisodesPerDayOut]
    requests: RequestFulfilmentOut
    top_tasks: list[TopTaskOut]
    meta: AnalyticsMetaOut
