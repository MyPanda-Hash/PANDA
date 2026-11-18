"""Add durable cross-process Pi execution admission leases."""

import sqlalchemy as sa

from alembic import op

revision = "20260921_0096"
down_revision = "20260916_0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pi_execution_admission_guard",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "pi_execution_admission_leases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_key", sa.String(length=64), nullable=False),
        sa.Column("lease_owner", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wait_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pi_execution_admission_status_queued",
        "pi_execution_admission_leases",
        ["status", "queued_at"],
    )
    op.create_index(
        "ix_pi_execution_admission_status_lease",
        "pi_execution_admission_leases",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_pi_execution_admission_user_status",
        "pi_execution_admission_leases",
        ["user_key", "status"],
    )
    guard = sa.table(
        "pi_execution_admission_guard",
        sa.column("id", sa.Integer()),
        sa.column("revision", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        guard.insert().values(id=1, revision=0, updated_at=sa.func.now())
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pi_execution_admission_user_status",
        table_name="pi_execution_admission_leases",
    )
    op.drop_index(
        "ix_pi_execution_admission_status_lease",
        table_name="pi_execution_admission_leases",
    )
    op.drop_index(
        "ix_pi_execution_admission_status_queued",
        table_name="pi_execution_admission_leases",
    )
    op.drop_table("pi_execution_admission_leases")
    op.drop_table("pi_execution_admission_guard")
