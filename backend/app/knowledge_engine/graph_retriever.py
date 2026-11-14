"""Knowledge Graph Retriever — graph-enhanced retrieval for writing assistance.

Expands seed entities via multi-hop graph traversal, detects communities
via Louvain algorithm, and assembles evidence packages grouped by topic.

Used by the writing service to precisely match old knowledge to user's
writing requirements.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field

import networkx as nx
from networkx.algorithms.community import louvain_communities
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.documents import Document, DocumentVersion
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeEntityDocument,
    KnowledgeRelation,
)

logger = logging.getLogger(__name__)

# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class ScoredEntity:
    entity_id: str
    name: str
    entity_type: str
    description: str | None
    aliases: list[str]
    properties: dict[str, str]
    score: float
    match_reason: str = ""


@dataclass
class ExpandedEntity:
    entity: ScoredEntity
    relevance_score: float
    hops_from_seed: int = 0


@dataclass
class KnowledgeCommunity:
    topic: str
    entities: list[ScoredEntity]
    cohesion_score: float
    entity_count: int


@dataclass
class EvidencePackage:
    section_objective: str
    communities: list[CommunityEvidence] = field(default_factory=list)
    total_entities: int = 0
    total_chunks: int = 0


@dataclass
class CommunityEvidence:
    topic: str
    cohesion: float
    entities: list[dict] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)


@dataclass
class SurpriseConnection:
    entity_a: ScoredEntity
    entity_b: ScoredEntity
    relevance_score: float
    common_neighbors: list[str]
    suggestion: str


@dataclass
class KnowledgeGap:
    concept: str
    coverage_score: float
    suggestion: str


@dataclass
class WritingContext:
    """Budget-aware context assembled for LLM writing prompt injection.

    Designed to fit within a configurable token budget by prioritizing
    the most relevant knowledge and compressing low-priority content.
    """

    system_prompt_enrichment: str = ""
    key_entities: list[str] = field(default_factory=list)
    community_briefs: list[str] = field(default_factory=list)
    suggested_claims: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    token_estimate: int = 0


# ── Token estimation helpers ────────────────────────────────────────

# Rough heuristic: mixed Chinese/English text → ~2 chars per token.
_CHARS_PER_TOKEN = 2.0


def _est_tokens(text: str) -> int:
    """Estimate token count for mixed Chinese/English text."""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


# ── Retriever ────────────────────────────────────────────────────────


class GraphRetriever:
    """Multi-hop graph traversal + community detection + evidence assembly."""

    def __init__(self, db: Session):
        self.db = db

    # ── Public API ───────────────────────────────────────────────────

    def match_and_expand(
        self,
        space_id: str,
        query: str,
        *,
        entity_types: list[str] | None = None,
        max_hops: int = 2,
        max_seeds: int = 20,
        max_expanded: int = 50,
    ) -> list[ScoredEntity]:
        """Match query to entities and expand via graph traversal.

        Returns combined list of seed + expanded entities ranked by relevance.
        """
        # Step 1: Match seeds
        seeds = self._match_seeds(space_id, query, entity_types, max_seeds)
        if not seeds:
            return []

        # Step 2: Multi-hop expansion
        expanded = self._expand(space_id, seeds, max_hops, max_expanded)

        # Step 3: Merge and rank
        all_entities = self._merge_and_rank(seeds, expanded)
        return all_entities

    def build_evidence_package(
        self,
        space_id: str,
        section_objective: str,
        matched_entities: list[ScoredEntity],
        *,
        max_communities: int = 5,
        max_entities_per_community: int = 10,
    ) -> EvidencePackage:
        """Build evidence package grouped by knowledge communities.

        Args:
            space_id: Knowledge space ID.
            section_objective: What the writing section aims to address.
            matched_entities: Entities from match_and_expand.
            max_communities: Max number of communities to include.
            max_entities_per_community: Max entities per community.
        """
        if not matched_entities:
            return EvidencePackage(section_objective=section_objective)

        # Step 1: Build subgraph from matched entities
        entity_ids = {e.entity_id for e in matched_entities}
        G, entity_map = self._build_subgraph(space_id, entity_ids)

        # Step 2: Detect communities
        communities = self._detect_communities(G, entity_map, max_communities)

        # Step 3: Assemble evidence
        package = EvidencePackage(section_objective=section_objective)
        for community in communities:
            if len(community.entities) > max_entities_per_community:
                community.entities = community.entities[:max_entities_per_community]

            ce = CommunityEvidence(
                topic=community.topic,
                cohesion=community.cohesion_score,
                entities=[
                    {
                        "name": e.name,
                        "type": e.entity_type,
                        "description": e.description or "",
                        "properties": e.properties,
                    }
                    for e in community.entities
                ],
                chunks=[],  # Chunks would be fetched from document_chunks table
            )
            package.communities.append(ce)
            package.total_entities += len(community.entities)

        return package

    def detect_surprises(
        self,
        space_id: str,
        matched_entities: list[ScoredEntity],
        *,
        max_surprises: int = 5,
        min_relevance: float = 0.3,
    ) -> list[SurpriseConnection]:
        """Detect surprising connections between entities.

        A surprise connection is when two entities are highly related
        (via shared neighbors, source overlap) but have no direct edge.
        """
        if len(matched_entities) < 2:
            return []

        entity_ids = {e.entity_id for e in matched_entities}
        G, entity_map = self._build_subgraph(space_id, entity_ids)

        surprises: list[SurpriseConnection] = []
        entities = list(entity_map.values())

        for i, ea in enumerate(entities):
            for eb in entities[i + 1 :]:
                # Skip if direct edge exists
                if G.has_edge(ea.entity_id, eb.entity_id):
                    continue

                # Compute relevance via shared neighbors + source overlap
                relevance = self._compute_pairwise_relevance(
                    space_id,
                    ea.entity_id,
                    eb.entity_id,
                    G,
                )
                if relevance < min_relevance:
                    continue

                common = list(nx.common_neighbors(G, ea.entity_id, eb.entity_id))
                common_names = [entity_map[n].name for n in common]

                surprises.append(
                    SurpriseConnection(
                        entity_a=ea,
                        entity_b=eb,
                        relevance_score=relevance,
                        common_neighbors=common_names,
                        suggestion=(
                            f"'{ea.name}' 和 '{eb.name}' 可能通过 {', '.join(common_names[:3])} 关联，"
                            f"建议在写作中探索二者的关系"
                        ),
                    )
                )

        surprises.sort(key=lambda s: s.relevance_score, reverse=True)
        return surprises[:max_surprises]

    def detect_gaps(
        self,
        space_id: str,
        concepts: list[str],
        matched_entities: list[ScoredEntity],
        *,
        coverage_threshold: float = 0.3,
    ) -> list[KnowledgeGap]:
        """Detect concepts that are poorly covered in the knowledge graph."""
        matched_names = {e.name.lower() for e in matched_entities}
        for e in matched_entities:
            for alias in e.aliases:
                matched_names.add(alias.lower())

        gaps: list[KnowledgeGap] = []
        for concept in concepts:
            concept_lower = concept.lower()
            # Check if any matched entity covers this concept
            best_score = 0.0
            for e in matched_entities:
                if concept_lower in e.name.lower():
                    best_score = max(best_score, e.score)
                elif concept_lower in (e.description or "").lower():
                    best_score = max(best_score, e.score * 0.5)

            if best_score < coverage_threshold:
                gaps.append(
                    KnowledgeGap(
                        concept=concept,
                        coverage_score=best_score,
                        suggestion=(
                            f"知识库中缺少关于 '{concept}' 的充分信息，"
                            f"建议补充相关文档（当前覆盖度: {best_score:.2f}）"
                        ),
                    )
                )

        return gaps

    def space_has_active_entities(self, space_id: str) -> bool:
        """True when the space already has at least one active knowledge entity."""
        row = self.db.scalar(
            select(KnowledgeEntity.id)
            .where(
                KnowledgeEntity.space_id == space_id,
                KnowledgeEntity.status == "active",
            )
            .limit(1)
        )
        return row is not None

    def documents_for_entities(
        self,
        space_id: str,
        entities: list[ScoredEntity],
    ) -> list[tuple[Document, DocumentVersion, float]]:
        """Resolve matched entities to ready, published documents with graph scores.

        Score per document = max(entity.score * link.relevance_score) with a small
        bonus when multiple entities point at the same document.
        """
        if not entities:
            return []

        entity_by_id = {e.entity_id: e for e in entities}
        entity_ids = list(entity_by_id.keys())
        links = list(
            self.db.scalars(
                select(KnowledgeEntityDocument).where(
                    KnowledgeEntityDocument.entity_id.in_(entity_ids)
                )
            ).all()
        )
        version_scores: dict[str, list[float]] = {}
        if not links:
            # Fall back to source_document_version_ids_json on entities
            for entity in entities:
                raw = self.db.get(KnowledgeEntity, entity.entity_id)
                if not raw:
                    continue
                for version_id in self._parse_json_list(raw.source_document_version_ids_json):
                    version_scores.setdefault(version_id, []).append(float(entity.score))
        else:
            for link in links:
                entity = entity_by_id.get(link.entity_id)
                if not entity:
                    continue
                contribution = float(entity.score) * float(link.relevance_score or 1.0)
                version_scores.setdefault(link.document_version_id, []).append(contribution)

        if not version_scores:
            return []

        rows = list(
            self.db.execute(
                select(Document, DocumentVersion)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(
                    Document.space_id == space_id,
                    Document.status == "active",
                    DocumentVersion.id.in_(list(version_scores.keys())),
                    DocumentVersion.processing_stage == "ready",
                    DocumentVersion.queryable.is_(True),
                    DocumentVersion.is_current_published.is_(True),
                )
            ).all()
        )

        scored: list[tuple[Document, DocumentVersion, float]] = []
        for document, version in rows:
            parts = version_scores.get(version.id) or []
            if not parts:
                continue
            base = max(parts)
            multi_bonus = 0.05 * max(0, len(parts) - 1)
            scored.append((document, version, base + multi_bonus))
        scored.sort(key=lambda item: (-item[2], item[0].id))
        return scored

    def assemble_writing_context(
        self,
        section_objective: str,
        matched_entities: list[ScoredEntity],
        evidence: EvidencePackage,
        surprises: list[SurpriseConnection],
        gaps: list[KnowledgeGap],
        *,
        max_tokens: int = 2500,
        min_entity_score: float = 0.3,
        max_key_entities: int = 8,
        max_claims: int = 5,
    ) -> WritingContext:
        """Assemble a budget-aware writing context for LLM prompt injection.

        Allocates the token budget across sections by priority:
        P0 — key entities (names + descriptions)
        P1 — community briefs (topic summaries)
        P2 — system prompt enrichment (background)
        P3 — suggested claims
        P4 — warnings (gaps, surprises)

        Returns a WritingContext ready for injection into the LLM prompt.
        """
        ctx = WritingContext()
        budget = max_tokens

        # ── P0: Key entities ─────────────────────────────────────────
        active = [e for e in matched_entities if e.score >= min_entity_score]
        active.sort(key=lambda e: e.score, reverse=True)
        active = active[:max_key_entities]

        key_lines: list[str] = []
        for e in active:
            desc = (e.description or "")[:120]
            aliases = f"（亦称：{'、'.join(e.aliases[:3])}）" if e.aliases else ""
            key_lines.append(f"- **{e.name}**（{e.entity_type}）{aliases}: {desc}")
        ctx.key_entities = [e.name for e in active]
        ctx.token_estimate += _est_tokens("\n".join(key_lines))
        budget -= _est_tokens("\n".join(key_lines))

        # ── P1: Community briefs ─────────────────────────────────────
        briefs: list[str] = []
        for ci, c in enumerate(evidence.communities, 1):
            entity_names = [e.get("name", "?") for e in c.entities[:5]]
            cohesion_label = (
                "紧密"
                if c.cohesion >= 0.6
                else "较松散"
                if c.cohesion >= 0.3
                else "弱关联"
            )
            brief = (
                f"### 知识主题 {ci}：{c.topic}\n"
                f"凝聚力：{cohesion_label}（{c.cohesion:.2f}），"
                f"关联实体：{'、'.join(entity_names)}"
            )
            briefs.append(brief)
        ctx.community_briefs = briefs[:5]
        ctx.token_estimate += _est_tokens("\n\n".join(briefs))
        budget -= _est_tokens("\n\n".join(briefs))

        # ── P2: System prompt enrichment ─────────────────────────────
        enrichment_parts: list[str] = []

        # Background: what knowledge domains are relevant
        domains = {e.entity_type for e in active}
        if domains:
            enrichment_parts.append(
                f"本次写作涉及以下知识领域：{'、'.join(sorted(domains))}。"
            )

        # Core knowledge summary
        if active:
            top_names = "、".join(e.name for e in active[:5])
            enrichment_parts.append(
                f"知识库中与「{section_objective}」最相关的核心概念为：{top_names}。"
            )

        # Community overview
        if evidence.communities:
            community_topics = "；".join(c.topic for c in evidence.communities[:3])
            enrichment_parts.append(f"相关知识可分为以下主题群：{community_topics}。")

        enrichment_parts.append(
            "请在写作中优先引用以上知识库中的概念和关系，"
            "确保内容与已有知识体系一致。对于知识库覆盖不足的部分，"
            "请明确标注为「推测」或「待补充」。"
        )

        ctx.system_prompt_enrichment = "\n\n".join(enrichment_parts)
        ctx.token_estimate += _est_tokens(ctx.system_prompt_enrichment)
        budget -= _est_tokens(ctx.system_prompt_enrichment)

        # ── P3: Suggested claims ─────────────────────────────────────
        claims: list[str] = []
        # From communities: suggest verifying relationships
        for c in evidence.communities[:3]:
            entity_names = [e.get("name", "?") for e in c.entities[:3]]
            if len(entity_names) >= 2:
                claims.append(
                    f"请验证以下概念之间的关系：{'、'.join(entity_names)}（主题：{c.topic}）"
                )

        # From surprises: suggest exploring connections
        for s in surprises[:2]:
            claims.append(
                f"请探索 '{s.entity_a.name}' 与 '{s.entity_b.name}' 之间可能存在的关联"
            )

        ctx.suggested_claims = claims[:max_claims]
        ctx.token_estimate += _est_tokens("\n".join(claims))
        budget -= _est_tokens("\n".join(claims))

        # ── P4: Warnings ─────────────────────────────────────────────
        warnings: list[str] = []

        # Low-cohesion communities
        for c in evidence.communities:
            if c.cohesion < 0.3:
                warnings.append(
                    f"⚠️ 主题「{c.topic}」内实体关联较弱（凝聚力={c.cohesion:.2f}），"
                    f"请谨慎引用，避免将弱关联概念强制捆绑"
                )

        # Knowledge gaps
        for g in gaps[:3]:
            warnings.append(
                f"⚠️ 知识库缺少关于「{g.concept}」的充分信息（覆盖度={g.coverage_score:.2f}），"
                f"如需引用请标注为待补充"
            )

        # Budget warning
        if budget < 0:
            warnings.append(
                f"⚠️ 知识上下文已超出 token 预算 {abs(budget)} tokens，"
                f"低优先级内容已被截断。建议缩小写作范围或分节生成。"
            )

        ctx.warnings = warnings
        ctx.token_estimate += _est_tokens("\n".join(warnings))

        return ctx

    # ── Internal methods ──────────────────────────────────────────────

    def _match_seeds(
        self,
        space_id: str,
        query: str,
        entity_types: list[str] | None,
        limit: int,
    ) -> list[ScoredEntity]:
        """Match query against entity names, aliases, and descriptions."""
        terms = query.split()
        from sqlalchemy import or_

        stmt = select(KnowledgeEntity).where(
            KnowledgeEntity.space_id == space_id,
            KnowledgeEntity.status == "active",
        )

        if entity_types:
            stmt = stmt.where(KnowledgeEntity.entity_type.in_(entity_types))

        conditions = []
        for term in terms:
            conditions.append(KnowledgeEntity.canonical_name.ilike(f"%{term}%"))
            conditions.append(KnowledgeEntity.aliases_json.ilike(f"%{term}%"))
            conditions.append(KnowledgeEntity.description.ilike(f"%{term}%"))
        stmt = stmt.where(or_(*conditions))

        stmt = stmt.order_by(KnowledgeEntity.confidence.desc()).limit(limit * 2)
        entities = self.db.execute(stmt).scalars().all()

        # Score and rank
        scored: list[ScoredEntity] = []
        query_lower = query.lower()
        for e in entities:
            score = 0.0
            reason = ""
            if query_lower in e.canonical_name.lower():
                score += 3.0
                reason = "名称精确匹配"
            elif any(t.lower() in e.canonical_name.lower() for t in terms):
                score += 2.0
                reason = "名称部分匹配"
            aliases = self._parse_json_list(e.aliases_json)
            if any(query_lower in a.lower() for a in aliases):
                score += 2.5
                reason = "别名匹配"
            elif any(t.lower() in (e.description or "").lower() for t in terms):
                score += 1.0
                reason = "描述匹配"

            if score > 0:
                scored.append(
                    ScoredEntity(
                        entity_id=e.id,
                        name=e.canonical_name,
                        entity_type=e.entity_type,
                        description=e.description,
                        aliases=aliases,
                        properties=self._parse_json_dict(e.properties_json),
                        score=score * e.confidence,
                        match_reason=reason,
                    )
                )

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

    def _expand(
        self,
        space_id: str,
        seeds: list[ScoredEntity],
        max_hops: int,
        max_expanded: int,
    ) -> list[ExpandedEntity]:
        """Multi-hop BFS expansion from seed entities."""
        visited: dict[str, tuple[float, int]] = {}  # entity_id -> (score, hops)
        seed_ids = {s.entity_id for s in seeds}

        for s in seeds:
            visited[s.entity_id] = (s.score, 0)

        queue = [(s.entity_id, s.score, 0) for s in seeds]

        while queue:
            current_id, current_score, hop = queue.pop(0)
            if hop >= max_hops:
                continue

            # Get neighbors
            neighbors = self._get_neighbors(space_id, current_id, limit=20)
            for neighbor_id, rel_weight in neighbors:
                if neighbor_id in seed_ids:
                    continue

                decay = 0.5 ** (hop + 1)
                new_score = current_score * rel_weight * decay

                if neighbor_id not in visited or new_score > visited[neighbor_id][0]:
                    visited[neighbor_id] = (new_score, hop + 1)
                    queue.append((neighbor_id, new_score, hop + 1))

        # Build expanded entities (exclude seeds)
        expanded = []
        for eid, (score, hops) in visited.items():
            if eid in seed_ids:
                continue
            entity = self.db.get(KnowledgeEntity, eid)
            if entity:
                expanded.append(
                    ExpandedEntity(
                        entity=ScoredEntity(
                            entity_id=entity.id,
                            name=entity.canonical_name,
                            entity_type=entity.entity_type,
                            description=entity.description,
                            aliases=self._parse_json_list(entity.aliases_json),
                            properties=self._parse_json_dict(entity.properties_json),
                            score=score,
                            match_reason=f"图谱扩展（{hops}跳）",
                        ),
                        relevance_score=score,
                        hops_from_seed=hops,
                    )
                )

        expanded.sort(key=lambda x: x.relevance_score, reverse=True)
        return expanded[:max_expanded]

    def _get_neighbors(
        self,
        space_id: str,
        entity_id: str,
        limit: int = 20,
    ) -> list[tuple[str, float]]:
        """Get neighboring entities with relation weights."""
        out = self.db.execute(
            select(KnowledgeRelation.object_entity_id, KnowledgeRelation.weight)
            .where(
                KnowledgeRelation.subject_entity_id == entity_id,
                KnowledgeRelation.space_id == space_id,
            )
            .order_by(KnowledgeRelation.weight.desc())
            .limit(limit)
        ).all()

        incoming = self.db.execute(
            select(KnowledgeRelation.subject_entity_id, KnowledgeRelation.weight)
            .where(
                KnowledgeRelation.object_entity_id == entity_id,
                KnowledgeRelation.space_id == space_id,
            )
            .order_by(KnowledgeRelation.weight.desc())
            .limit(limit)
        ).all()

        neighbors: dict[str, float] = {}
        for nid, w in out:
            neighbors[nid] = max(neighbors.get(nid, 0), w)
        for nid, w in incoming:
            neighbors[nid] = max(neighbors.get(nid, 0), w)

        return sorted(neighbors.items(), key=lambda x: x[1], reverse=True)[:limit]

    def _merge_and_rank(
        self,
        seeds: list[ScoredEntity],
        expanded: list[ExpandedEntity],
    ) -> list[ScoredEntity]:
        """Merge seeds and expanded, rank by score."""
        all_entities = list(seeds)
        for ee in expanded:
            all_entities.append(ee.entity)
        all_entities.sort(key=lambda x: x.score, reverse=True)
        return all_entities

    def _build_subgraph(
        self,
        space_id: str,
        entity_ids: set[str],
    ) -> tuple[nx.Graph, dict[str, ScoredEntity]]:
        """Build a NetworkX graph from a set of entity IDs."""
        G = nx.Graph()
        entity_map: dict[str, ScoredEntity] = {}

        entities = (
            self.db.execute(
                select(KnowledgeEntity).where(KnowledgeEntity.id.in_(entity_ids))
            )
            .scalars()
            .all()
        )

        for e in entities:
            se = ScoredEntity(
                entity_id=e.id,
                name=e.canonical_name,
                entity_type=e.entity_type,
                description=e.description,
                aliases=self._parse_json_list(e.aliases_json),
                properties=self._parse_json_dict(e.properties_json),
                score=0.0,
            )
            entity_map[e.id] = se
            G.add_node(e.id, weight=se.score)

        # Add edges between entities in the subgraph
        relations = (
            self.db.execute(
                select(KnowledgeRelation).where(
                    KnowledgeRelation.subject_entity_id.in_(entity_ids),
                    KnowledgeRelation.object_entity_id.in_(entity_ids),
                    KnowledgeRelation.space_id == space_id,
                )
            )
            .scalars()
            .all()
        )

        for r in relations:
            if r.subject_entity_id in entity_ids and r.object_entity_id in entity_ids:
                G.add_edge(r.subject_entity_id, r.object_entity_id, weight=r.weight)

        return G, entity_map

    def _detect_communities(
        self,
        G: nx.Graph,
        entity_map: dict[str, ScoredEntity],
        max_communities: int,
    ) -> list[KnowledgeCommunity]:
        """Run Louvain community detection on the subgraph."""
        if G.number_of_edges() == 0:
            # No edges: each entity is its own community, theme by type
            communities = []
            for nid in G.nodes():
                e = entity_map.get(nid)
                if e:
                    communities.append(
                        KnowledgeCommunity(
                            topic=e.name,
                            entities=[e],
                            cohesion_score=0.0,
                            entity_count=1,
                        )
                    )
            return communities[:max_communities]

        try:
            raw_communities = louvain_communities(G, weight="weight", seed=42)
        except Exception:
            logger.warning(
                "Louvain community detection failed, falling back to single community"
            )
            raw_communities = [set(G.nodes())]

        result: list[KnowledgeCommunity] = []
        for node_set in raw_communities:
            entities = [entity_map[n] for n in node_set if n in entity_map]
            if not entities:
                continue

            cohesion = self._compute_cohesion(G, node_set)
            topic = self._derive_topic(entities)

            result.append(
                KnowledgeCommunity(
                    topic=topic,
                    entities=entities,
                    cohesion_score=cohesion,
                    entity_count=len(entities),
                )
            )

        result.sort(key=lambda c: c.cohesion_score, reverse=True)
        return result[:max_communities]

    @staticmethod
    def _compute_cohesion(G: nx.Graph, node_set: set[str]) -> float:
        """Compute community cohesion: internal edges / total edges."""
        internal = 0
        external = 0
        for n in node_set:
            for neighbor in G.neighbors(n):
                if neighbor in node_set:
                    internal += 1
                else:
                    external += 1
        total = internal + external
        return internal / total if total > 0 else 0.0

    @staticmethod
    def _derive_topic(entities: list[ScoredEntity]) -> str:
        """Derive a human-readable topic name from a community of entities."""
        if not entities:
            return "未命名主题"
        if len(entities) == 1:
            return entities[0].name
        # Use the highest-scored entity name as the topic
        sorted_entities = sorted(entities, key=lambda e: e.score, reverse=True)
        primary = sorted_entities[0].name
        others = [e.name for e in sorted_entities[1:3]]
        if others:
            return f"{primary} 及相关主题（{', '.join(others)}）"
        return primary

    def _compute_pairwise_relevance(
        self,
        space_id: str,
        entity_a_id: str,
        entity_b_id: str,
        G: nx.Graph,
    ) -> float:
        """Compute relevance between two entities using the 4-signal model."""
        # 1. Source overlap
        source_overlap = self._source_overlap_score(entity_a_id, entity_b_id)

        # 2. Adamic-Adar (shared neighbors)
        try:
            common = list(nx.common_neighbors(G, entity_a_id, entity_b_id))
            aa_score = 0.0
            for n in common:
                degree = G.degree(n)
                if degree > 1:
                    aa_score += 1.0 / math.log(degree)
            aa_score = aa_score / max(
                1.0,
                len(list(G.neighbors(entity_a_id)))
                + len(list(G.neighbors(entity_b_id))),
            )
        except Exception:
            aa_score = 0.0

        # 3. Type affinity
        ea = self.db.get(KnowledgeEntity, entity_a_id)
        eb = self.db.get(KnowledgeEntity, entity_b_id)
        type_affinity = 0.0
        if ea and eb:
            if ea.entity_type == eb.entity_type:
                type_affinity = 0.3
            affinity_matrix = {
                ("product", "technology"): 0.5,
                ("product", "organization"): 0.4,
                ("regulation", "product"): 0.6,
                ("concept", "technology"): 0.5,
            }
            type_affinity = max(
                type_affinity,
                affinity_matrix.get(
                    (ea.entity_type, eb.entity_type),
                    0.0,
                ),
            )

        # Weighted combination
        return source_overlap * 0.4 + aa_score * 0.3 + type_affinity * 0.2

    def _source_overlap_score(self, entity_a_id: str, entity_b_id: str) -> float:
        """Compute source document overlap between two entities."""
        ea = self.db.get(KnowledgeEntity, entity_a_id)
        eb = self.db.get(KnowledgeEntity, entity_b_id)
        if not ea or not eb:
            return 0.0

        docs_a = set(self._parse_json_list(ea.source_document_version_ids_json))
        docs_b = set(self._parse_json_list(eb.source_document_version_ids_json))
        if not docs_a or not docs_b:
            return 0.0

        return len(docs_a & docs_b) / len(docs_a | docs_b)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_json_list(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _parse_json_dict(raw: str | None) -> dict[str, str]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
