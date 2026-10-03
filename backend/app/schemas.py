"""Request and response bodies.

Input and output models are separate. No output model has a password_hash
field, so a hash can never end up in a response, whatever a route returns.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["client", "operator", "admin"]


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
