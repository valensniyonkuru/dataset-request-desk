"""initial schema

Revision ID: f95cc18aecf7
Revises:
Create Date: 2026-10-03 12:35:48.322988

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f95cc18aecf7"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REQUEST_STATUSES = "('submitted', 'in_progress', 'delivered', 'accepted', 'rejected')"

# CHECK constraint names are wrapped in op.f() ("final name"). Without it,
# Alembic applies the models' naming convention "ck_<table>_<name>" again and
# the constraint becomes e.g. "ck_users_ck_users_role".


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("organisation", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("role IN ('client', 'operator', 'admin')", name=op.f("ck_users_role")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
    )

    op.create_table(
        "import_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.Text(), nullable=False),
        sa.Column("started_by", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_import_runs"),
        sa.ForeignKeyConstraint(
            ["started_by"], ["users.id"], name="fk_import_runs_started_by_users", ondelete="RESTRICT"
        ),
    )

    op.create_table(
        "episodes",
        sa.Column("episode_id", sa.Text(), nullable=False),
        sa.Column("robot_id", sa.Text(), nullable=False),
        sa.Column("task_name", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.Text(), nullable=False),
        sa.Column("quality", sa.Text(), nullable=False),
        sa.Column("import_run_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("episode_id", name="pk_episodes"),
        sa.ForeignKeyConstraint(
            ["import_run_id"], ["import_runs.id"], name="fk_episodes_import_run_id_import_runs", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("duration_seconds > 0", name=op.f("ck_episodes_duration_positive")),
        sa.CheckConstraint("quality IN ('good', 'usable', 'bad')", name=op.f("ck_episodes_quality")),
    )
    op.create_index("ix_episodes_recorded_at", "episodes", ["recorded_at"])
    op.create_index("ix_episodes_robot_id_recorded_at", "episodes", ["robot_id", "recorded_at"])
    op.create_index("ix_episodes_task_name_quality", "episodes", ["task_name", "quality"])
    # Partial index: only good episodes, for "top task names by good episodes".
    op.create_index(
        "ix_episodes_task_name_good", "episodes", ["task_name"], postgresql_where=sa.text("quality = 'good'")
    )

    op.create_table(
        "requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("task_name", sa.Text(), nullable=False),
        sa.Column("episodes_requested", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'submitted'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_requests"),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"], name="fk_requests_client_id_users", ondelete="RESTRICT"),
        sa.CheckConstraint("episodes_requested > 0", name=op.f("ck_requests_episodes_requested_positive")),
        sa.CheckConstraint(f"status IN {REQUEST_STATUSES}", name=op.f("ck_requests_status")),
    )
    op.create_index("ix_requests_client_id_status", "requests", ["client_id", "status"])
    op.create_index("ix_requests_status_created_at", "requests", ["status", "created_at"])

    op.create_table(
        "assignments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("episode_id", sa.Text(), nullable=False),
        sa.Column("assigned_by", sa.BigInteger(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_assignments"),
        # An episode can be assigned to at most one request.
        sa.UniqueConstraint("episode_id", name="uq_assignments_episode_id"),
        sa.ForeignKeyConstraint(
            ["request_id"], ["requests.id"], name="fk_assignments_request_id_requests", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.episode_id"], name="fk_assignments_episode_id_episodes", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], name="fk_assignments_assigned_by_users", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_assignments_request_id", "assignments", ["request_id"])

    op.create_table(
        "request_status_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.BigInteger(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_request_status_history"),
        sa.ForeignKeyConstraint(
            ["request_id"], ["requests.id"], name="fk_request_status_history_request_id_requests", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"], ["users.id"], name="fk_request_status_history_changed_by_users", ondelete="RESTRICT"
        ),
        # A NULL from_status (the creation row) passes this CHECK.
        sa.CheckConstraint(f"from_status IN {REQUEST_STATUSES}", name=op.f("ck_request_status_history_from_status")),
        sa.CheckConstraint(f"to_status IN {REQUEST_STATUSES}", name=op.f("ck_request_status_history_to_status")),
    )
    op.create_index(
        "ix_request_status_history_request_id_changed_at", "request_status_history", ["request_id", "changed_at"]
    )


def downgrade() -> None:
    # Reverse order of creation, so no table is dropped while another still
    # references it. Dropping a table also drops its indexes and constraints.
    op.drop_table("request_status_history")
    op.drop_table("assignments")
    op.drop_table("requests")
    op.drop_table("episodes")
    op.drop_table("import_runs")
    op.drop_table("users")
