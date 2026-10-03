"""Database tables, as typed SQLAlchemy 2.0 models.

Business rules that the database can enforce live here as constraints
(CHECK, UNIQUE, FOREIGN KEY), so they hold no matter which code writes the row.
Enumerations are TEXT + CHECK instead of native Postgres enums, because
changing a CHECK in a later migration is simpler than altering an enum type.

The schema itself is created by the Alembic migration in migrations/versions/.
These models must stay in sync with it (`alembic check` compares the two).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names, e.g. "ck_users_role" or "fk_requests_client_id_users".
# Tests and error handling can then refer to a constraint by name.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('client', 'operator', 'admin')", name="role"),
        # Emails are stored lowercase, which makes the UNIQUE constraint case-insensitive.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    organisation: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    # Users are never deleted, only deactivated.
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    file_name: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text)
    # Null when the import was started from the command line, not by a user.
    started_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
        CheckConstraint("quality IN ('good', 'usable', 'bad')", name="quality"),
        Index("ix_episodes_recorded_at", "recorded_at"),
        Index("ix_episodes_robot_id_recorded_at", "robot_id", "recorded_at"),
        Index("ix_episodes_task_name_quality", "task_name", "quality"),
        # Partial index: only good episodes, for "top task names by good episodes".
        Index("ix_episodes_task_name_good", "task_name", postgresql_where=text("quality = 'good'")),
    )

    # Natural key from the recording system's export, e.g. "EP-00156".
    # Re-importing the same file finds the existing row instead of duplicating it.
    episode_id: Mapped[str] = mapped_column(Text, primary_key=True)
    robot_id: Mapped[str] = mapped_column(Text)
    task_name: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    operator_name: Mapped[str] = mapped_column(Text)
    quality: Mapped[str] = mapped_column(Text)
    import_run_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint("episodes_requested > 0", name="episodes_requested_positive"),
        CheckConstraint(
            "status IN ('submitted', 'in_progress', 'delivered', 'accepted', 'rejected')",
            name="status",
        ),
        Index("ix_requests_client_id_status", "client_id", "status"),
        Index("ix_requests_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    task_name: Mapped[str] = mapped_column(Text)
    episodes_requested: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'submitted'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # onupdate only applies to updates made through the ORM, which is the only writer.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (Index("ix_assignments_request_id", "request_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="RESTRICT"))
    # UNIQUE: an episode can be assigned to at most one request.
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.episode_id", ondelete="RESTRICT"), unique=True
    )
    assigned_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequestStatusHistory(Base):
    """Audit trail of status changes. Rows are only ever inserted, never updated or deleted."""

    __tablename__ = "request_status_history"
    __table_args__ = (
        # A NULL from_status passes the CHECK, which is what we want for the creation row.
        CheckConstraint(
            "from_status IN ('submitted', 'in_progress', 'delivered', 'accepted', 'rejected')",
            name="from_status",
        ),
        CheckConstraint(
            "to_status IN ('submitted', 'in_progress', 'delivered', 'accepted', 'rejected')",
            name="to_status",
        ),
        Index("ix_request_status_history_request_id_changed_at", "request_id", "changed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="RESTRICT"))
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
