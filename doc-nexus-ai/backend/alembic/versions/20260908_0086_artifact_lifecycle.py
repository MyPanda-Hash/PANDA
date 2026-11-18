"""Recoverable artifact removal and authenticated, revocable sharing."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0086"
down_revision = "20260904_0085"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("artifacts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("artifacts", sa.Column("share_id", sa.String(64), nullable=True))
    op.add_column("artifacts", sa.Column("share_scope", sa.String(32), nullable=True))
    op.add_column("artifacts", sa.Column("share_space_ids_json", sa.Text(), nullable=True))
    op.add_column("artifacts", sa.Column("share_checksum", sa.String(64), nullable=True))
    op.create_index("ix_artifacts_share_id", "artifacts", ["share_id"], unique=True)

def downgrade() -> None:
    op.drop_index("ix_artifacts_share_id", table_name="artifacts")
    with op.batch_alter_table("artifacts") as batch:
        for name in ("share_checksum", "share_space_ids_json", "share_scope", "share_id", "deleted_at"):
            batch.drop_column(name)
