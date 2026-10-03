"""Request and response bodies.

Input and output models are separate. No output model has a password_hash
field, so a hash can never end up in a response, whatever a route returns.
"""

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.workflow import Status

Role = Literal["client", "operator", "admin"]

# Deliberately simple: one "@", no spaces, a dot in the domain. Good enough to
# catch typos in an internal tool, without an extra dependency.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ErrorOut(BaseModel):
    """Body of every error response except 422. Used only to document errors in /docs."""

    detail: str


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")  # e.g. a client_id in the body is rejected

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
    model_config = ConfigDict(extra="forbid")

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
