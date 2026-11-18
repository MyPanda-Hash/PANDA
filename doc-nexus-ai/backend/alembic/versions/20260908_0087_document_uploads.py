"""Add durable MinIO multipart document uploads.

Revision ID: 20260908_0087
Revises: 20260908_0086
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_0087"
down_revision = "20260908_0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_upload_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("space_id", sa.String(length=64), nullable=False),
        sa.Column("version_id", sa.String(length=64), nullable=True),
        sa.Column("document_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("source_session_id", sa.String(length=64), nullable=True),
        sa.Column("client_upload_id", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=True),
        sa.Column("source_relative_path", sa.Text(), nullable=False),
        sa.Column("part_size", sa.Integer(), nullable=False),
        sa.Column("part_count", sa.Integer(), nullable=False),
        sa.Column("staging_bucket", sa.String(length=128), nullable=True),
        sa.Column("staging_object_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("multipart_upload_id", sa.String(length=512), nullable=True),
        sa.Column("canonical_bucket", sa.String(length=128), nullable=True),
        sa.Column("canonical_object_key", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="uploading"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["space_id"], ["knowledge_spaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["document_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["document_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["document_categories.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "client_upload_id",
            name="uq_document_upload_sessions_owner_client",
        ),
        sa.UniqueConstraint("version_id", name="uq_document_upload_sessions_version"),
    )
    op.create_index(
        "ix_document_upload_sessions_owner_user_id",
        "document_upload_sessions",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_document_upload_sessions_space_id",
        "document_upload_sessions",
        ["space_id"],
    )
    op.create_index(
        "ix_document_upload_sessions_version_id",
        "document_upload_sessions",
        ["version_id"],
    )
    op.create_index(
        "ix_document_upload_sessions_document_id",
        "document_upload_sessions",
        ["document_id"],
    )
    op.create_index(
        "ix_document_upload_sessions_job_id",
        "document_upload_sessions",
        ["job_id"],
    )
    op.create_index(
        "ix_document_upload_sessions_source_session_id",
        "document_upload_sessions",
        ["source_session_id"],
    )
    op.create_index(
        "ix_document_upload_sessions_category_id",
        "document_upload_sessions",
        ["category_id"],
    )
    op.create_index(
        "ix_document_upload_sessions_status",
        "document_upload_sessions",
        ["status"],
    )
    op.create_index(
        "ix_document_upload_sessions_expires_at",
        "document_upload_sessions",
        ["expires_at"],
    )
    op.create_index(
        "ix_document_upload_sessions_updated_at",
        "document_upload_sessions",
        ["updated_at"],
    )
    op.create_index(
        "ix_document_upload_sessions_status_lease",
        "document_upload_sessions",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_document_upload_sessions_owner_created",
        "document_upload_sessions",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_document_upload_sessions_expires_status",
        "document_upload_sessions",
        ["expires_at", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_upload_sessions_expires_status",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_owner_created",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_status_lease",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_updated_at",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_expires_at",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_status",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_category_id",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_source_session_id",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_job_id",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_document_id",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_version_id",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_space_id",
        table_name="document_upload_sessions",
    )
    op.drop_index(
        "ix_document_upload_sessions_owner_user_id",
        table_name="document_upload_sessions",
    )
    op.drop_table("document_upload_sessions")
