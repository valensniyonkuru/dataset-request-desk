"""Request and response bodies.

Input and output models are separate. No output model has a password_hash
field, so a hash can never end up in a response, whatever a route returns.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["client", "operator", "admin"]

# Deliberately simple: one "@", no spaces, a dot in the domain. Good enough to
# catch typos in an internal tool, without an extra dependency.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


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
