"""Knowledge Graph — entities, relations, and entity-document links.

Revision ID: 20260730_0013
Revises: 20260730_0012
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0013"
down_revision = "20260730_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- knowledge_entities ---
    op.create_table(
        "knowledge_entities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "space_id",
            sa.String(64),
            sa.ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_name", sa.String(512), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("aliases_json", sa.Text(), nullable=True),
        sa.Column("properties_json", sa.Text(), nullable=True),
        sa.Column("source_document_version_ids_json", sa.Text(), nullable=True),
        sa.Column("source_chunk_ids_json", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "merged_into_id",
            sa.String(64),
            sa.ForeignKey("knowledge_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ke_space_id", "knowledge_entities", ["space_id"])
    op.create_index("ix_ke_canonical_name", "knowledge_entities", ["canonical_name"])
    op.create_index(
        "ix_ke_space_type", "knowledge_entities", ["space_id", "entity_type"]
    )
    op.create_index("ix_ke_name", "knowledge_entities", ["space_id", "canonical_name"])

    # --- knowledge_relations ---
    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "space_id",
            sa.String(64),
            sa.ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_entity_id",
            sa.String(64),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column(
            "object_entity_id",
            sa.String(64),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_chunk_ids_json", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subject_entity_id",
            "relation_type",
            "object_entity_id",
            name="uq_kr_subject_type_object",
        ),
    )
    op.create_index("ix_kr_space_id", "knowledge_relations", ["space_id"])
    op.create_index("ix_kr_subject", "knowledge_relations", ["subject_entity_id"])
    op.create_index("ix_kr_object", "knowledge_relations", ["object_entity_id"])

    # --- knowledge_entity_documents ---
    op.create_table(
        "knowledge_entity_documents",
        sa.Column(
            "entity_id",
            sa.String(64),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_version_id",
            sa.String(64),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "entity_id", "document_version_id", name="uq_ked_entity_doc"
        ),
    )


def downgrade() -> None:
    op.drop_table("knowledge_entity_documents")
    op.drop_table("knowledge_relations")
    op.drop_table("knowledge_entities")
