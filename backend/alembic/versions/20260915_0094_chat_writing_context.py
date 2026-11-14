"""Persist explicit project-writing intent separately from artifact linkage.

NULL preserves ordinary chat semantics for every pre-existing message. Never
backfill from writing_project_id, which also links automatically created files.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260915_0094"
down_revision = "20260915_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("writing_context_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "writing_context_json")
