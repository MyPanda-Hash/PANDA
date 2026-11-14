"""Knowledge Graph models — entities, relations, and entity-document links.

Entities are canonical concepts extracted from documents by LLM.
Relations connect entities with typed, weighted edges.
Entity-document links track which documents contribute to each entity.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base


class KnowledgeEntity(Base):
    __tablename__ = "knowledge_entities"
    __table_args__ = (
        CheckConstraint(
            "entity_type in ('person','organization','product','technology','concept','event','location','regulation')",
            name="ck_ke_entity_type",
        ),
        CheckConstraint(
            "status in ('active','merged','deprecated')", name="ck_ke_status"
        ),
        Index("ix_ke_space_type", "space_id", "entity_type"),
        Index("ix_ke_name", "space_id", "canonical_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    space_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True
    )
    canonical_name: Mapped[str] = mapped_column(String(512), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON fields stored as Text; SQLite-compatible, PostgreSQL handles JSON natively
    aliases_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document_version_ids_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    source_chunk_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    merged_into_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Relationships
    relations_out: Mapped[list[KnowledgeRelation]] = relationship(
        back_populates="subject_entity",
        foreign_keys="KnowledgeRelation.subject_entity_id",
        cascade="all, delete-orphan",
    )
    relations_in: Mapped[list[KnowledgeRelation]] = relationship(
        back_populates="object_entity",
        foreign_keys="KnowledgeRelation.object_entity_id",
        cascade="all, delete-orphan",
    )


class KnowledgeRelation(Base):
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        CheckConstraint(
            "relation_type in ('depends_on','part_of','causes','supports','contradicts','related_to','same_as','instance_of')",
            name="ck_kr_relation_type",
        ),
        UniqueConstraint(
            "subject_entity_id",
            "relation_type",
            "object_entity_id",
            name="uq_kr_subject_type_object",
        ),
        Index("ix_kr_space", "space_id"),
        Index("ix_kr_subject", "subject_entity_id"),
        Index("ix_kr_object", "object_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    space_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True
    )
    subject_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(64))
    object_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_chunk_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    subject_entity: Mapped[KnowledgeEntity] = relationship(
        back_populates="relations_out",
        foreign_keys=[subject_entity_id],
    )
    object_entity: Mapped[KnowledgeEntity] = relationship(
        back_populates="relations_in",
        foreign_keys=[object_entity_id],
    )


class KnowledgeEntityDocument(Base):
    __tablename__ = "knowledge_entity_documents"
    __table_args__ = (
        UniqueConstraint("entity_id", "document_version_id", name="uq_ked_entity_doc"),
    )

    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), primary_key=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
