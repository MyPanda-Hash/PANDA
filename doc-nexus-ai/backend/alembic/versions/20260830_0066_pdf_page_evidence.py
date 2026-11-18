"""Add PDF page, OCR block, and citation location evidence."""

from alembic import context, op
import sqlalchemy as sa

revision = "20260830_0066"
down_revision = "20260830_0065"
branch_labels = None
depends_on = None


def _inspector():
    return None if context.is_offline_mode() else sa.inspect(op.get_bind())


def _tables() -> set[str]:
    inspector = _inspector()
    return set() if inspector is None else set(inspector.get_table_names())


def _columns(table: str) -> set[str]:
    inspector = _inspector()
    if inspector is None or table not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if "document_pages" not in _tables():
        op.create_table(
            "document_pages",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("document_version_id", sa.String(length=64), nullable=False),
            sa.Column("page_number", sa.Integer(), nullable=False),
            sa.Column("width_points", sa.Float(), nullable=False),
            sa.Column("height_points", sa.Float(), nullable=False),
            sa.Column("rotation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("extraction_method", sa.String(length=16), nullable=False),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("average_confidence", sa.Float(), nullable=True),
            sa.Column("warning_code", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("document_version_id", "page_number", name="uq_document_pages_version_number"),
            sa.CheckConstraint("page_number > 0", name="ck_document_pages_page_number"),
            sa.CheckConstraint("width_points > 0 AND height_points > 0", name="ck_document_pages_dimensions"),
            sa.CheckConstraint("rotation in (0, 90, 180, 270)", name="ck_document_pages_rotation"),
            sa.CheckConstraint("quality_score >= 0 AND quality_score <= 1", name="ck_document_pages_quality"),
            sa.CheckConstraint("average_confidence IS NULL OR (average_confidence >= 0 AND average_confidence <= 1)", name="ck_document_pages_confidence"),
            sa.CheckConstraint("extraction_method in ('native', 'ocr')", name="ck_document_pages_method"),
        )
        op.create_index("ix_document_pages_document_version_id", "document_pages", ["document_version_id"])
        op.create_index("ix_document_pages_version", "document_pages", ["document_version_id", "page_number"])
    if "document_page_blocks" not in _tables():
        op.create_table(
            "document_page_blocks",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("page_id", sa.String(length=64), nullable=False),
            sa.Column("block_index", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("bbox_x0", sa.Float(), nullable=True),
            sa.Column("bbox_y0", sa.Float(), nullable=True),
            sa.Column("bbox_x1", sa.Float(), nullable=True),
            sa.Column("bbox_y1", sa.Float(), nullable=True),
            sa.Column("char_start", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("char_end", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["page_id"], ["document_pages.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("page_id", "block_index", name="uq_document_page_blocks_page_index"),
            sa.CheckConstraint("block_index >= 0", name="ck_document_page_blocks_index"),
            sa.CheckConstraint("source in ('native', 'ocr')", name="ck_document_page_blocks_source"),
            sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_document_page_blocks_confidence"),
            sa.CheckConstraint("char_start >= 0 AND char_end >= char_start", name="ck_document_page_blocks_span"),
            sa.CheckConstraint("source = 'native' OR (bbox_x0 IS NOT NULL AND bbox_y0 IS NOT NULL AND bbox_x1 IS NOT NULL AND bbox_y1 IS NOT NULL)", name="ck_document_page_blocks_ocr_bbox"),
            sa.CheckConstraint("(bbox_x0 IS NULL AND bbox_y0 IS NULL AND bbox_x1 IS NULL AND bbox_y1 IS NULL) OR (bbox_x0 >= 0 AND bbox_y0 >= 0 AND bbox_x1 <= 1 AND bbox_y1 <= 1 AND bbox_x1 >= bbox_x0 AND bbox_y1 >= bbox_y0)", name="ck_document_page_blocks_bbox"),
        )
        op.create_index("ix_document_page_blocks_page_id", "document_page_blocks", ["page_id"])
        op.create_index("ix_document_page_blocks_page", "document_page_blocks", ["page_id", "block_index"])

    version_columns = _columns("document_versions")
    for name, column in (
        ("extraction_method", sa.Column("extraction_method", sa.String(length=16), nullable=True)),
        ("page_count", sa.Column("page_count", sa.Integer(), nullable=False, server_default="0")),
        ("ocr_page_count", sa.Column("ocr_page_count", sa.Integer(), nullable=False, server_default="0")),
        ("extraction_warning_code", sa.Column("extraction_warning_code", sa.String(length=64), nullable=True)),
    ):
        if context.is_offline_mode() or (version_columns and name not in version_columns):
            op.add_column("document_versions", column)

    citation_columns = _columns("citations")
    for name, column in (
        ("page_number", sa.Column("page_number", sa.Integer(), nullable=True)),
        ("page_block_id", sa.Column("page_block_id", sa.String(length=64), nullable=True)),
        ("bbox_json", sa.Column("bbox_json", sa.Text(), nullable=True)),
    ):
        if context.is_offline_mode() or (citation_columns and name not in citation_columns):
            op.add_column("citations", column)
    if not context.is_offline_mode() and "page_block_id" not in citation_columns:
        bind = op.get_bind()
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("citations", recreate="always") as batch:
                batch.create_foreign_key(
                    "fk_citations_page_block_id",
                    "document_page_blocks",
                    ["page_block_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
        else:
            op.create_foreign_key(
                "fk_citations_page_block_id", "citations", "document_page_blocks", ["page_block_id"], ["id"], ondelete="SET NULL"
            )
    if not context.is_offline_mode():
        inspector = _inspector()
        indexes = {item["name"] for item in inspector.get_indexes("citations")} if inspector else set()
        if "ix_citations_page_block_id" not in indexes:
            op.create_index("ix_citations_page_block_id", "citations", ["page_block_id"])


def downgrade() -> None:
    # Expand-first: keep page evidence during application rollback.
    pass
