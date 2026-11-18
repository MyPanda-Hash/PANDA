"""Create immutable document and document-version records.

Revision ID: 20260727_0004
Revises: 20260727_0003
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0004"
down_revision = "20260727_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("space_id", sa.String(length=64), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["document_categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["space_id"], ["knowledge_spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_space_id", "documents", ["space_id"])
    op.create_index("ix_documents_category_id", "documents", ["category_id"])
    op.create_index("ix_documents_name", "documents", ["name"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_created_by_user_id", "documents", ["created_by_user_id"])
    op.create_index("ix_documents_updated_at", "documents", ["updated_at"])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("processing_stage", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("queryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_of_version_id", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("is_current_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(sa.or_(sa.column("queryable", sa.Boolean()).is_(False), sa.column("processing_stage", sa.String()) == "ready"), name="ck_document_versions_queryable_ready"),
        sa.CheckConstraint(sa.or_(sa.column("is_current_published", sa.Boolean()).is_(False), sa.column("processing_stage", sa.String()) == "ready"), name="ck_document_versions_published_ready"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["retry_of_version_id"], ["document_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_versions_document_number"),
        sa.UniqueConstraint("retry_of_version_id", name="uq_document_versions_retry_of"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_processing_stage", "document_versions", ["processing_stage"])
    op.create_index("ix_document_versions_queryable", "document_versions", ["queryable"])
    op.create_index("ix_document_versions_retry_of_version_id", "document_versions", ["retry_of_version_id"])
    op.create_index("ix_document_versions_is_current_published", "document_versions", ["is_current_published"])
    op.create_index(
        "uq_document_versions_current_published",
        "document_versions",
        ["document_id"],
        unique=True,
        sqlite_where=sa.column("is_current_published", sa.Boolean()).is_(True),
        postgresql_where=sa.column("is_current_published", sa.Boolean()).is_(True),
    )
    op.create_table(
        "document_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("version_id", sa.String(length=64), nullable=False),
        sa.Column("space_id", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False, server_default="document_processing"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["knowledge_spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", name="uq_document_jobs_version"),
    )
    op.create_index("ix_document_jobs_document_id", "document_jobs", ["document_id"])
    op.create_index("ix_document_jobs_version_id", "document_jobs", ["version_id"])
    op.create_index("ix_document_jobs_space_id", "document_jobs", ["space_id"])
    op.create_index("ix_document_jobs_created_by_user_id", "document_jobs", ["created_by_user_id"])
    op.create_index("ix_document_jobs_status", "document_jobs", ["status"])
    op.create_index("ix_document_jobs_updated_at", "document_jobs", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_document_jobs_updated_at", table_name="document_jobs")
    op.drop_index("ix_document_jobs_status", table_name="document_jobs")
    op.drop_index("ix_document_jobs_created_by_user_id", table_name="document_jobs")
    op.drop_index("ix_document_jobs_space_id", table_name="document_jobs")
    op.drop_index("ix_document_jobs_version_id", table_name="document_jobs")
    op.drop_index("ix_document_jobs_document_id", table_name="document_jobs")
    op.drop_table("document_jobs")
    op.drop_index("uq_document_versions_current_published", table_name="document_versions")
    op.drop_index("ix_document_versions_is_current_published", table_name="document_versions")
    op.drop_index("ix_document_versions_queryable", table_name="document_versions")
    op.drop_index("ix_document_versions_retry_of_version_id", table_name="document_versions")
    op.drop_index("ix_document_versions_processing_stage", table_name="document_versions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_documents_updated_at", table_name="documents")
    op.drop_index("ix_documents_created_by_user_id", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_name", table_name="documents")
    op.drop_index("ix_documents_category_id", table_name="documents")
    op.drop_index("ix_documents_space_id", table_name="documents")
    op.drop_table("documents")
