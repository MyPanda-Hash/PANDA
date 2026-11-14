"""Allow page evidence to record bounded vision fallback output."""

from alembic import context, op
import sqlalchemy as sa


revision = "20260830_0067"
down_revision = "20260830_0066"
branch_labels = None
depends_on = None


def _constraints(table: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {
        str(item.get("name"))
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def _expand_checks(
    table: str,
    *,
    drops: tuple[str, ...],
    creates: tuple[tuple[str, str], ...],
) -> None:
    if context.is_offline_mode():
        for name in drops:
            op.drop_constraint(name, table_name=table, type_="check")
        for name, expression in creates:
            op.create_check_constraint(name, table, expression)
        return
    existing = _constraints(table)
    if not existing:
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            for name in drops:
                if name in existing:
                    batch.drop_constraint(name, type_="check")
            for name, expression in creates:
                batch.create_check_constraint(name, expression)
    else:
        for name in drops:
            if name in existing:
                op.drop_constraint(name, table_name=table, type_="check")
        for name, expression in creates:
            op.create_check_constraint(name, table, expression)


def upgrade() -> None:
    _expand_checks(
        "document_pages",
        drops=("ck_document_pages_method",),
        creates=(
            (
                "ck_document_pages_method",
                "extraction_method in ('native', 'ocr', 'vision')",
            ),
        ),
    )
    _expand_checks(
        "document_page_blocks",
        drops=(
            "ck_document_page_blocks_source",
            "ck_document_page_blocks_ocr_bbox",
        ),
        creates=(
            (
                "ck_document_page_blocks_source",
                "source in ('native', 'ocr', 'vision')",
            ),
            (
                "ck_document_page_blocks_ocr_bbox",
                "source in ('native', 'vision') OR (bbox_x0 IS NOT NULL AND bbox_y0 IS NOT NULL AND bbox_x1 IS NOT NULL AND bbox_y1 IS NOT NULL)",
            ),
        ),
    )
    _expand_checks(
        "document_versions",
        drops=("ck_document_versions_extraction_method",),
        creates=(
            (
                "ck_document_versions_extraction_method",
                "extraction_method is null or extraction_method in ('native', 'ocr', 'vision', 'mixed')",
            ),
        ),
    )


def downgrade() -> None:
    # Keep already indexed vision page evidence on rollback; the application
    # can stop producing new rows via the feature flag.
    pass
