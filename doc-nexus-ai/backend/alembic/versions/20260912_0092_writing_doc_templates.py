"""PPTX template assets: shared metadata plus a per-revision compile snapshot.

Two tables land together because the revision table carries both the durable
upload session and the durable compile work item — there is no separate job or
outbox table, and template work never enters the knowledge-document pipeline.
``writing_projects.pptx_template_revision_id`` is the only project-side change;
existing projects stay NULL and keep rendering with the built-in theme.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260912_0092"
down_revision = "20260912_0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_doc_templates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "visibility", sa.String(32), nullable=False, server_default="private"
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("create_idempotency_key", sa.String(256), nullable=False),
        sa.Column("create_request_sha256", sa.String(64), nullable=False),
        sa.Column("skill_key", sa.String(64), nullable=False, server_default="*"),
        sa.Column(
            "output_format", sa.String(16), nullable=False, server_default="pptx"
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "visibility in ('private', 'company_shared')",
            name="ck_writing_doc_templates_visibility",
        ),
        sa.CheckConstraint(
            "output_format in ('docx', 'pptx')",
            name="ck_writing_doc_templates_output_format",
        ),
        sa.CheckConstraint(
            "status in ('active', 'archived')",
            name="ck_writing_doc_templates_status",
        ),
        sa.UniqueConstraint(
            "created_by_user_id",
            "create_idempotency_key",
            name="uq_writing_doc_templates_create_idempotency",
        ),
    )
    op.create_index(
        "ix_writing_doc_templates_created_by_user_id",
        "writing_doc_templates",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_writing_doc_templates_owner_updated",
        "writing_doc_templates",
        ["created_by_user_id", "updated_at"],
    )
    op.create_index(
        "ix_writing_doc_templates_visibility_status",
        "writing_doc_templates",
        ["visibility", "status", "updated_at"],
    )

    op.create_table(
        "writing_doc_template_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "template_id",
            sa.String(64),
            sa.ForeignKey("writing_doc_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("request_sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_format", sa.String(16), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "expected_source_sha256", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column(
            "expected_source_size_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("upload_backend", sa.String(32), nullable=False, server_default=""),
        sa.Column(
            "staging_object_key", sa.String(512), nullable=False, server_default=""
        ),
        sa.Column("multipart_upload_id", sa.String(256), nullable=True),
        sa.Column(
            "part_size_bytes", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "upload_manifest_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column("source_storage_key", sa.String(512), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "source_size_bytes", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("render_package_key", sa.String(512), nullable=True),
        sa.Column(
            "render_package_sha256", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column("compiler_version", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "pack_schema_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("compile_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("pack_sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "sample_selection_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "retained_elements_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column("slot_map_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("edit_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "prepared_edit_revision", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("placeholders_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "state", sa.String(32), nullable=False, server_default="uploading"
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_user_id", sa.String(64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("compile_stage", sa.String(32), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message_safe", sa.String(500), nullable=True),
        sa.Column(
            "source_slide_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("health_grade", sa.String(4), nullable=False, server_default=""),
        sa.Column(
            "health_warnings_json", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "cleanup_manifest_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "preview_manifest_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "preview_state", sa.String(16), nullable=False, server_default="missing"
        ),
        sa.Column("preview_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preview_lease_owner", sa.String(128), nullable=True),
        sa.Column("preview_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview_error_code", sa.String(64), nullable=True),
        sa.Column("preview_error_message_safe", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state in ('uploading', 'pending', 'compiling', "
            "'awaiting_confirmation', 'published', 'failed')",
            name="ck_writing_doc_revision_state",
        ),
        sa.CheckConstraint(
            "source_format in ('pptx', 'potx')",
            name="ck_writing_doc_revision_source_format",
        ),
        sa.CheckConstraint(
            "preview_state in ('missing', 'pending', 'generating', 'ready', 'failed')",
            name="ck_writing_doc_revision_preview_state",
        ),
        sa.UniqueConstraint(
            "template_id", "revision_no", name="uq_writing_doc_revision_number"
        ),
        sa.UniqueConstraint(
            "template_id",
            "idempotency_key",
            name="uq_writing_doc_revision_idempotency",
        ),
    )
    op.create_index(
        "ix_writing_doc_template_revisions_template_id",
        "writing_doc_template_revisions",
        ["template_id"],
    )
    op.create_index(
        "ix_writing_doc_template_revisions_state",
        "writing_doc_template_revisions",
        ["state"],
    )
    op.create_index(
        "ix_writing_doc_revisions_work",
        "writing_doc_template_revisions",
        ["state", "next_attempt_at"],
    )
    op.create_index(
        "ix_writing_doc_revisions_preview_work",
        "writing_doc_template_revisions",
        ["preview_state", "updated_at"],
    )

    with op.batch_alter_table("writing_projects") as batch:
        batch.add_column(
            sa.Column("pptx_template_revision_id", sa.String(64), nullable=True)
        )
        batch.create_index(
            "ix_writing_projects_pptx_template_revision_id",
            ["pptx_template_revision_id"],
        )
        batch.create_foreign_key(
            "fk_writing_projects_pptx_template_revision",
            "writing_doc_template_revisions",
            ["pptx_template_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    # SQLite batch mode reflects the table and then re-creates every index it
    # saw, so the column's index has to be dropped explicitly before the
    # column — otherwise the recreated table has no such column to index.
    # On PostgreSQL dropping the column already removes both.
    with op.batch_alter_table("writing_projects") as batch:
        batch.drop_index("ix_writing_projects_pptx_template_revision_id")
        batch.drop_column("pptx_template_revision_id")
    for name in (
        "ix_writing_doc_revisions_preview_work",
        "ix_writing_doc_revisions_work",
        "ix_writing_doc_template_revisions_state",
        "ix_writing_doc_template_revisions_template_id",
    ):
        op.drop_index(name, table_name="writing_doc_template_revisions")
    op.drop_table("writing_doc_template_revisions")
    for name in (
        "ix_writing_doc_templates_visibility_status",
        "ix_writing_doc_templates_owner_updated",
        "ix_writing_doc_templates_created_by_user_id",
    ):
        op.drop_index(name, table_name="writing_doc_templates")
    op.drop_table("writing_doc_templates")
