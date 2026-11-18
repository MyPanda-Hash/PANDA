"""Knowledge Graph API — query entities, relations, and graph insights."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import transaction
from app.models.auth import User
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeEntityDocument,
    KnowledgeRelation,
)
from app.services.authorization import PermissionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge-graph"], prefix="/knowledge-graph")


# ── Schemas ──────────────────────────────────────────────────────────


class EntitySummary(BaseModel):
    id: str
    canonical_name: str
    entity_type: str
    description: str | None
    aliases: list[str] = Field(default_factory=list)
    confidence: float
    document_count: int = 0

    model_config = {"from_attributes": True}


class EntityDetail(EntitySummary):
    properties: dict[str, str] = Field(default_factory=dict)
    source_document_version_ids: list[str] = Field(default_factory=list)
    relations: list[RelationSummary] = Field(default_factory=list)
    related_entities: list[EntitySummary] = Field(default_factory=list)


class RelationSummary(BaseModel):
    id: str
    relation_type: str
    description: str | None
    subject_name: str
    object_name: str
    confidence: float

    model_config = {"from_attributes": True}


class GraphStats(BaseModel):
    entity_count: int
    relation_count: int
    entity_types: dict[str, int]
    relation_types: dict[str, int]


class KnowledgeMatchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    entity_types: list[str] | None = None
    max_hops: int = Field(default=2, ge=1, le=3)
    limit: int = Field(default=20, ge=1, le=50)


class KnowledgeMatchResult(BaseModel):
    entities: list[EntitySummary]
    relations: list[RelationSummary]
    total_matched: int


# ── Helpers ──────────────────────────────────────────────────────────


def _entity_to_summary(e: KnowledgeEntity, doc_count: int = 0) -> EntitySummary:
    return EntitySummary(
        id=e.id,
        canonical_name=e.canonical_name,
        entity_type=e.entity_type,
        description=e.description,
        aliases=_parse_json_list(e.aliases_json),
        confidence=e.confidence,
        document_count=doc_count,
    )


def _entity_to_detail(
    db: Session,
    e: KnowledgeEntity,
    doc_count: int = 0,
) -> EntityDetail:
    relations = []
    related_ids: set[str] = set()

    for r in e.relations_out or []:
        relations.append(
            RelationSummary(
                id=r.id,
                relation_type=r.relation_type,
                description=r.description,
                subject_name=e.canonical_name,
                object_name=r.object_entity.canonical_name if r.object_entity else "",
                confidence=r.confidence,
            )
        )
        if r.object_entity:
            related_ids.add(r.object_entity_id)

    for r in e.relations_in or []:
        relations.append(
            RelationSummary(
                id=r.id,
                relation_type=r.relation_type,
                description=r.description,
                subject_name=r.subject_entity.canonical_name
                if r.subject_entity
                else "",
                object_name=e.canonical_name,
                confidence=r.confidence,
            )
        )
        if r.subject_entity:
            related_ids.add(r.subject_entity_id)

    related = []
    if related_ids:
        related_entities = (
            db.execute(
                select(KnowledgeEntity).where(KnowledgeEntity.id.in_(related_ids))
            )
            .scalars()
            .all()
        )
        related = [_entity_to_summary(re) for re in related_entities]

    return EntityDetail(
        id=e.id,
        canonical_name=e.canonical_name,
        entity_type=e.entity_type,
        description=e.description,
        aliases=_parse_json_list(e.aliases_json),
        confidence=e.confidence,
        document_count=doc_count,
        properties=_parse_json_dict(e.properties_json),
        source_document_version_ids=_parse_json_list(
            e.source_document_version_ids_json
        ),
        relations=relations,
        related_entities=related,
    )


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_json_dict(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _get_doc_count(db: Session, entity_id: str) -> int:
    return (
        db.scalar(
            select(func.count()).where(
                KnowledgeEntityDocument.entity_id == entity_id,
            )
        )
        or 0
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/spaces/{space_id}/stats", response_model=GraphStats)
def get_graph_stats(space_id: str, user: User = Depends(current_user)) -> GraphStats:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )

        entity_count = (
            db.scalar(
                select(func.count()).where(
                    KnowledgeEntity.space_id == space_id,
                    KnowledgeEntity.status == "active",
                )
            )
            or 0
        )

        relation_count = (
            db.scalar(
                select(func.count()).where(
                    KnowledgeRelation.space_id == space_id,
                )
            )
            or 0
        )

        # Entity type distribution
        type_rows = db.execute(
            select(KnowledgeEntity.entity_type, func.count())
            .where(
                KnowledgeEntity.space_id == space_id, KnowledgeEntity.status == "active"
            )
            .group_by(KnowledgeEntity.entity_type)
        ).all()
        entity_types = {row[0]: row[1] for row in type_rows}

        # Relation type distribution
        rel_rows = db.execute(
            select(KnowledgeRelation.relation_type, func.count())
            .where(KnowledgeRelation.space_id == space_id)
            .group_by(KnowledgeRelation.relation_type)
        ).all()
        relation_types = {row[0]: row[1] for row in rel_rows}

        return GraphStats(
            entity_count=entity_count,
            relation_count=relation_count,
            entity_types=entity_types,
            relation_types=relation_types,
        )


@router.get("/spaces/{space_id}/entities", response_model=list[EntitySummary])
def list_entities(
    space_id: str,
    query: str | None = Query(default=None, max_length=500),
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
) -> list[EntitySummary]:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )

        stmt = select(KnowledgeEntity).where(
            KnowledgeEntity.space_id == space_id,
            KnowledgeEntity.status == "active",
        )

        if entity_type:
            stmt = stmt.where(KnowledgeEntity.entity_type == entity_type)

        if query:
            stmt = stmt.where(KnowledgeEntity.canonical_name.ilike(f"%{query}%"))

        stmt = stmt.order_by(KnowledgeEntity.confidence.desc()).limit(limit)
        entities = db.execute(stmt).scalars().all()

        return [_entity_to_summary(e, _get_doc_count(db, e.id)) for e in entities]


@router.get("/spaces/{space_id}/entities/{entity_id}", response_model=EntityDetail)
def get_entity(
    space_id: str,
    entity_id: str,
    user: User = Depends(current_user),
) -> EntityDetail:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )

        entity = db.get(KnowledgeEntity, entity_id)
        if not entity or entity.space_id != space_id:
            raise HTTPException(status_code=404, detail="实体不存在")

        return _entity_to_detail(db, entity, _get_doc_count(db, entity.id))


@router.get(
    "/spaces/{space_id}/entities/{entity_id}/related",
    response_model=list[EntitySummary],
)
def get_related_entities(
    space_id: str,
    entity_id: str,
    max_hops: int = Query(default=1, ge=1, le=2),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
) -> list[EntitySummary]:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )

        entity = db.get(KnowledgeEntity, entity_id)
        if not entity or entity.space_id != space_id:
            raise HTTPException(status_code=404, detail="实体不存在")

        visited: set[str] = {entity_id}
        current: set[str] = {entity_id}

        for _ in range(max_hops):
            next_ids: set[str] = set()
            # Find outgoing relations
            out = (
                db.execute(
                    select(KnowledgeRelation.object_entity_id).where(
                        KnowledgeRelation.subject_entity_id.in_(current),
                        KnowledgeRelation.space_id == space_id,
                    )
                )
                .scalars()
                .all()
            )
            next_ids.update(out)

            # Find incoming relations
            incoming = (
                db.execute(
                    select(KnowledgeRelation.subject_entity_id).where(
                        KnowledgeRelation.object_entity_id.in_(current),
                        KnowledgeRelation.space_id == space_id,
                    )
                )
                .scalars()
                .all()
            )
            next_ids.update(incoming)

            next_ids -= visited
            if not next_ids:
                break
            visited.update(next_ids)
            current = next_ids

        related_ids = visited - {entity_id}
        if not related_ids:
            return []

        related = (
            db.execute(
                select(KnowledgeEntity)
                .where(
                    KnowledgeEntity.id.in_(related_ids),
                    KnowledgeEntity.status == "active",
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )

        return [_entity_to_summary(e, _get_doc_count(db, e.id)) for e in related]


@router.post("/spaces/{space_id}/match", response_model=KnowledgeMatchResult)
def match_knowledge(
    space_id: str,
    body: KnowledgeMatchRequest,
    user: User = Depends(current_user),
) -> KnowledgeMatchResult:
    """Match a user query against the knowledge graph for writing assistance."""
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )

        # Keyword + name match
        stmt = select(KnowledgeEntity).where(
            KnowledgeEntity.space_id == space_id,
            KnowledgeEntity.status == "active",
        )

        if body.entity_types:
            stmt = stmt.where(KnowledgeEntity.entity_type.in_(body.entity_types))

        # Search by canonical name and aliases
        terms = body.query.split()
        conditions = []
        for term in terms:
            conditions.append(KnowledgeEntity.canonical_name.ilike(f"%{term}%"))
            conditions.append(KnowledgeEntity.aliases_json.ilike(f"%{term}%"))
            conditions.append(KnowledgeEntity.description.ilike(f"%{term}%"))

        from sqlalchemy import or_

        stmt = stmt.where(or_(*conditions))

        entities = db.execute(stmt.limit(body.limit)).scalars().all()

        if not entities:
            return KnowledgeMatchResult(entities=[], relations=[], total_matched=0)

        entity_ids = {e.id for e in entities}

        # Get relations between matched entities
        if len(entity_ids) > 1:
            relations = (
                db.execute(
                    select(KnowledgeRelation).where(
                        KnowledgeRelation.subject_entity_id.in_(entity_ids),
                        KnowledgeRelation.object_entity_id.in_(entity_ids),
                        KnowledgeRelation.space_id == space_id,
                    )
                )
                .scalars()
                .all()
            )

            relation_summaries = [
                RelationSummary(
                    id=r.id,
                    relation_type=r.relation_type,
                    description=r.description,
                    subject_name=r.subject_entity.canonical_name
                    if r.subject_entity
                    else "",
                    object_name=r.object_entity.canonical_name
                    if r.object_entity
                    else "",
                    confidence=r.confidence,
                )
                for r in relations
            ]
        else:
            relation_summaries = []

        return KnowledgeMatchResult(
            entities=[
                _entity_to_summary(e, _get_doc_count(db, e.id)) for e in entities
            ],
            relations=relation_summaries,
            total_matched=len(entities),
        )
