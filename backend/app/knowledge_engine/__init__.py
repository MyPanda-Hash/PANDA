"""Knowledge Graph Compiler — two-step LLM-driven entity & relation extraction.

Step 1 (Analyze): LLM reads document, extracts entities with descriptions.
Step 2 (Build):   LLM builds relations between entities, referencing existing graph.

The compiler runs after local document chunking, as an additional stage
in the document processing pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.documents import DocumentVersion
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeEntityDocument,
    KnowledgeRelation,
)

logger = logging.getLogger(__name__)

ENTITY_TYPES = [
    "person",
    "organization",
    "product",
    "technology",
    "concept",
    "event",
    "location",
    "regulation",
]

RELATION_TYPES = [
    "depends_on",
    "part_of",
    "causes",
    "supports",
    "contradicts",
    "related_to",
    "same_as",
    "instance_of",
]

# ── Prompts ──────────────────────────────────────────────────────────

ENTITY_EXTRACTION_SYSTEM = """你是一个知识工程师。请从文档中提取结构化的知识实体。

## 实体类型
- person: 人名
- organization: 公司、部门、团队、机构
- product: 具体产品、型号、硬件设备、软件系统
- technology: 技术方案、算法、架构模式、协议
- concept: 抽象概念、业务术语、方法论、指标
- event: 重要事件、里程碑、会议
- location: 地点、区域、国家
- regulation: 法规、标准、合规要求、政策

## 输出要求
以 JSON 数组输出，每个实体包含：
- canonical_name: 规范名称（唯一标识）
- entity_type: 上述类型之一
- aliases: 别名列表（文档中出现的同义表达）
- description: 200-500字精炼描述，包含关键属性和上下文
- properties: 结构化属性键值对，如 {"额定功率": "100kW", "发布时间": "2024-01"}
- confidence: 0.0-1.0 置信度
- relation_to_existing: "new"（新实体）| "merge"（与已有实体合并）| "supplement"（补充已有实体）
- existing_entity_id: 如果 merge 或 supplement，填写已有实体ID，否则 null

## 已有实体（供参考，判断是否重复或可合并）
{existing_entities_context}

## 注意
- 只提取文档中明确提到的实体，不要凭空编造
- 属性值必须来自文档原文
- 如果文档中没有足够信息，confidence 应低于 0.5
- 同一概念的不同表述应合并为一个实体，用 aliases 记录"""

RELATION_EXTRACTION_SYSTEM = """你是一个知识关系工程师。请分析实体之间的关系。

## 关系类型
- depends_on: A依赖B（A需要B才能工作/存在）
- part_of: A是B的组成部分
- causes: A导致/引起B
- supports: A支持/证明/增强B
- contradicts: A与B矛盾/冲突
- related_to: 一般关联（不属于以上类型但有关联）
- same_as: A与B是同一事物
- instance_of: A是B的一个实例/具体实现

## 输出要求
以 JSON 数组输出，每个关系包含：
- subject: 主体实体 canonical_name
- relation_type: 关系类型
- object: 客体实体 canonical_name
- description: 关系说明（一句话）
- confidence: 0.0-1.0
- source_chunk_ids: 支持该关系的原文chunk ID列表（如果有）

## 实体列表
{entities_context}

## 注意
- 只输出实体间有明确文本依据的关系
- 不要重复已有关系
- 如果双向关系都有意义，选择更自然的表达方向"""

# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class RawEntity:
    canonical_name: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    relation_to_existing: str = "new"
    existing_entity_id: str | None = None


@dataclass
class RawRelation:
    subject: str
    relation_type: str
    object: str
    description: str = ""
    confidence: float = 1.0
    source_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class CompilationResult:
    new_entities: int = 0
    merged_entities: int = 0
    supplemented_entities: int = 0
    new_relations: int = 0
    errors: list[str] = field(default_factory=list)


# ── Compiler ─────────────────────────────────────────────────────────


class KnowledgeCompiler:
    """Compiles structured knowledge from document text into the knowledge graph."""

    def __init__(self, db: Session, llm_call: Any = None):
        self.db = db
        self.llm = llm_call  # async callable (system_prompt, user_prompt) -> str

    def compile(
        self,
        document_version: DocumentVersion,
        document_text: str,
        chunk_ids: list[str],
    ) -> CompilationResult:
        """Run two-step compilation on a document version.

        Args:
            document_version: The DocumentVersion being processed.
            document_text: Full extracted text of the document.
            chunk_ids: Chunk IDs from the local pipeline for source tracing.
        """
        space_id = document_version.document.space_id
        result = CompilationResult()
        if not space_id:
            return result
        # Use a savepoint so flush/constraint failures do not poison the caller's
        # outer transaction (document ready must remain independent of graph compile).
        nested = self.db.begin_nested()
        try:
            # 1. Find candidate existing entities
            existing = self._find_candidates(space_id, document_text, top_k=30)

            # 2. Step 1: Extract entities
            raw_entities = self._extract_entities(document_text, existing)
            if not raw_entities:
                logger.info(
                    "No entities extracted from document %s",
                    document_version.document_id,
                )
                nested.commit()
                return result

            # 3. Merge/upsert entities
            entity_map = self._persist_entities(
                raw_entities, existing, space_id, document_version, result
            )

            # 4. Step 2: Extract relations
            raw_relations = self._extract_relations(document_text, entity_map)
            if raw_relations:
                self._persist_relations(
                    raw_relations, entity_map, space_id, chunk_ids, result
                )
            nested.commit()
        except Exception as e:
            nested.rollback()
            logger.exception(
                "Knowledge compilation failed for document %s",
                document_version.document_id,
            )
            result.errors.append(str(e))

        return result

    # ── helpers ──────────────────────────────────────────────────────

    def _find_candidates(
        self,
        space_id: str,
        text: str,
        top_k: int = 30,
    ) -> list[KnowledgeEntity]:
        """Find existing entities that might overlap with new document content."""
        # Simple keyword-based candidate lookup: match entity names in text
        stmt = (
            select(KnowledgeEntity)
            .where(
                KnowledgeEntity.space_id == space_id,
                KnowledgeEntity.status == "active",
            )
            .order_by(KnowledgeEntity.updated_at.desc())
            .limit(200)
        )
        all_entities = self.db.execute(stmt).scalars().all()

        # Score by name overlap with document text
        scored: list[tuple[float, KnowledgeEntity]] = []
        text_lower = text.lower()
        for e in all_entities:
            score = 0.0
            if e.canonical_name.lower() in text_lower:
                score += 2.0
            aliases = self._parse_json_list(e.aliases_json)
            for alias in aliases:
                if alias.lower() in text_lower:
                    score += 1.0
            if score > 0:
                scored.append((score, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def _extract_entities(
        self,
        text: str,
        existing: list[KnowledgeEntity],
    ) -> list[RawEntity]:
        """Step 1: LLM extracts entities from document text."""
        existing_context = self._format_existing_entities(existing)
        user_prompt = (
            f"## 文档内容\n\n{text[:12000]}\n\n## 提取要求\n请按JSON格式输出实体列表。"
        )

        if not self.llm:
            return self._fallback_entity_extraction(text)

        try:
            response = self.llm(
                ENTITY_EXTRACTION_SYSTEM.format(
                    existing_entities_context=existing_context,
                ),
                user_prompt,
            )
            return self._parse_entity_response(response)
        except Exception as e:
            logger.warning("LLM entity extraction failed: %s", e)
            return self._fallback_entity_extraction(text)

    def _extract_relations(
        self,
        text: str,
        entity_map: dict[str, str],
    ) -> list[RawRelation]:
        """Step 2: LLM extracts relations between entities."""
        if not entity_map:
            return []

        entities_context = "\n".join(
            f"- {name} ({entity_map[name]})" for name in entity_map
        )
        user_prompt = (
            f"## 文档内容\n\n{text[:8000]}\n\n## 提取要求\n请按JSON格式输出关系列表。"
        )

        if not self.llm:
            return []

        try:
            response = self.llm(
                RELATION_EXTRACTION_SYSTEM.format(
                    entities_context=entities_context,
                ),
                user_prompt,
            )
            return self._parse_relation_response(response, entity_map)
        except Exception as e:
            logger.warning("LLM relation extraction failed: %s", e)
            return []

    def _persist_entities(
        self,
        raw: list[RawEntity],
        existing: list[KnowledgeEntity],
        space_id: str,
        doc_version: DocumentVersion,
        result: CompilationResult,
    ) -> dict[str, str]:
        """Persist entities, handling merges and supplements. Returns {name: entity_id}."""
        entity_map: dict[str, str] = {}
        existing_by_name: dict[str, KnowledgeEntity] = {}
        for e in existing:
            existing_by_name[e.canonical_name.lower()] = e
            aliases = self._parse_json_list(e.aliases_json)
            for a in aliases:
                existing_by_name[a.lower()] = e

        now_ts = now()

        for raw_e in raw:
            name_key = raw_e.canonical_name.lower()

            if raw_e.relation_to_existing == "merge" and raw_e.existing_entity_id:
                # Merge: update existing entity
                existing_entity = self.db.get(KnowledgeEntity, raw_e.existing_entity_id)
                if existing_entity:
                    self._merge_entity(existing_entity, raw_e, doc_version, now_ts)
                    entity_map[raw_e.canonical_name] = existing_entity.id
                    result.merged_entities += 1
                    continue

            if raw_e.relation_to_existing == "supplement" and raw_e.existing_entity_id:
                existing_entity = self.db.get(KnowledgeEntity, raw_e.existing_entity_id)
                if existing_entity:
                    self._supplement_entity(existing_entity, raw_e, doc_version, now_ts)
                    entity_map[raw_e.canonical_name] = existing_entity.id
                    result.supplemented_entities += 1
                    continue

            # Check if name matches existing entity
            existing_entity = existing_by_name.get(name_key)
            if existing_entity:
                self._supplement_entity(existing_entity, raw_e, doc_version, now_ts)
                entity_map[raw_e.canonical_name] = existing_entity.id
                result.supplemented_entities += 1
                continue

            # New entity
            entity = KnowledgeEntity(
                id=f"ent_{new_token(12)}",
                space_id=space_id,
                canonical_name=raw_e.canonical_name,
                entity_type=raw_e.entity_type,
                description=raw_e.description,
                aliases_json=json.dumps(raw_e.aliases, ensure_ascii=False),
                properties_json=json.dumps(raw_e.properties, ensure_ascii=False),
                source_document_version_ids_json=json.dumps([doc_version.id]),
                source_chunk_ids_json=json.dumps([]),
                confidence=raw_e.confidence,
                status="active",
                created_at=now_ts,
                updated_at=now_ts,
            )
            self.db.add(entity)
            self.db.flush()

            # Link entity to document
            self.db.add(
                KnowledgeEntityDocument(
                    entity_id=entity.id,
                    document_version_id=doc_version.id,
                    relevance_score=raw_e.confidence,
                    created_at=now_ts,
                )
            )

            entity_map[raw_e.canonical_name] = entity.id
            result.new_entities += 1

        return entity_map

    def _persist_relations(
        self,
        raw: list[RawRelation],
        entity_map: dict[str, str],
        space_id: str,
        chunk_ids: list[str],
        result: CompilationResult,
    ) -> None:
        """Persist relations, skipping duplicates."""
        now_ts = now()

        for raw_r in raw:
            subj_id = entity_map.get(raw_r.subject)
            obj_id = entity_map.get(raw_r.object)
            if not subj_id or not obj_id:
                continue
            if raw_r.relation_type not in RELATION_TYPES:
                continue

            # Check for duplicate
            existing = self.db.execute(
                select(KnowledgeRelation).where(
                    KnowledgeRelation.subject_entity_id == subj_id,
                    KnowledgeRelation.relation_type == raw_r.relation_type,
                    KnowledgeRelation.object_entity_id == obj_id,
                )
            ).scalar_one_or_none()
            if existing:
                continue

            relation = KnowledgeRelation(
                id=f"rel_{new_token(12)}",
                space_id=space_id,
                subject_entity_id=subj_id,
                relation_type=raw_r.relation_type,
                object_entity_id=obj_id,
                description=raw_r.description,
                source_chunk_ids_json=json.dumps(raw_r.source_chunk_ids or chunk_ids),
                confidence=raw_r.confidence,
                weight=raw_r.confidence,
                created_at=now_ts,
            )
            self.db.add(relation)
            result.new_relations += 1

    # ── merge helpers ────────────────────────────────────────────────

    def _merge_entity(
        self,
        entity: KnowledgeEntity,
        raw: RawEntity,
        doc_version: DocumentVersion,
        now_ts: datetime,
    ) -> None:
        """Merge raw entity into existing: update description, add aliases, add source."""
        if raw.description and len(raw.description) > len(entity.description or ""):
            entity.description = raw.description

        existing_aliases = set(self._parse_json_list(entity.aliases_json))
        for a in raw.aliases:
            if a.lower() not in {x.lower() for x in existing_aliases}:
                existing_aliases.add(a)
        entity.aliases_json = json.dumps(sorted(existing_aliases), ensure_ascii=False)

        existing_props = self._parse_json_dict(entity.properties_json)
        existing_props.update(raw.properties)
        entity.properties_json = json.dumps(existing_props, ensure_ascii=False)

        self._add_source(entity, doc_version.id, now_ts)
        entity.updated_at = now_ts

    def _supplement_entity(
        self,
        entity: KnowledgeEntity,
        raw: RawEntity,
        doc_version: DocumentVersion,
        now_ts: datetime,
    ) -> None:
        """Supplement existing entity with additional info from new document."""
        if raw.description and entity.description:
            entity.description = entity.description + "\n\n" + raw.description
        elif raw.description:
            entity.description = raw.description

        existing_aliases = set(self._parse_json_list(entity.aliases_json))
        for a in raw.aliases:
            existing_aliases.add(a)
        entity.aliases_json = json.dumps(sorted(existing_aliases), ensure_ascii=False)

        existing_props = self._parse_json_dict(entity.properties_json)
        for k, v in raw.properties.items():
            if k not in existing_props:
                existing_props[k] = v
        entity.properties_json = json.dumps(existing_props, ensure_ascii=False)

        self._add_source(entity, doc_version.id, now_ts)
        entity.updated_at = now_ts

    def _add_source(
        self, entity: KnowledgeEntity, doc_version_id: str, now_ts: datetime
    ) -> None:
        """Add document version to entity's source list and entity-document link."""
        source_ids = self._parse_json_list(entity.source_document_version_ids_json)
        if doc_version_id not in source_ids:
            source_ids.append(doc_version_id)
            entity.source_document_version_ids_json = json.dumps(source_ids)

        existing_link = self.db.execute(
            select(KnowledgeEntityDocument).where(
                KnowledgeEntityDocument.entity_id == entity.id,
                KnowledgeEntityDocument.document_version_id == doc_version_id,
            )
        ).scalar_one_or_none()
        if not existing_link:
            self.db.add(
                KnowledgeEntityDocument(
                    entity_id=entity.id,
                    document_version_id=doc_version_id,
                    relevance_score=0.8,
                    created_at=now_ts,
                )
            )

    # ── formatting helpers ───────────────────────────────────────────

    @staticmethod
    def _format_existing_entities(entities: list[KnowledgeEntity]) -> str:
        if not entities:
            return "（暂无已有实体）"
        lines = []
        for e in entities[:30]:
            aliases = KnowledgeCompiler._parse_json_list(e.aliases_json)
            alias_str = "、".join(aliases[:5]) if aliases else "无"
            lines.append(
                f"- [{e.entity_type}] {e.canonical_name} (ID: {e.id})\n"
                f"  别名: {alias_str}\n"
                f"  描述: {(e.description or '')[:200]}"
            )
        return "\n".join(lines)

    # ── parsing helpers ──────────────────────────────────────────────

    @staticmethod
    def _safe_float(value: object, default: float = 0.5) -> float:
        try:
            return float(value)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return default

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

    @staticmethod
    def _parse_entity_response(response: str) -> list[RawEntity]:
        """Parse LLM JSON response into RawEntity list."""
        # Try to extract JSON array from response
        text = response.strip()
        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text)
            if not isinstance(data, list):
                data = [data]
        except json.JSONDecodeError as exc:
            del exc
            # Try to find JSON array in the text
            import re

            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError as exc:
                    del exc
                    return []
            else:
                return []

        entities = []
        for item in data:
            if not isinstance(item, dict):
                continue
            entities.append(
                RawEntity(
                    canonical_name=str(item.get("canonical_name", "")).strip(),
                    entity_type=str(item.get("entity_type", "concept")).strip(),
                    aliases=item.get("aliases", []) or [],
                    description=str(item.get("description", "")).strip(),
                    properties=item.get("properties", {}) or {},
                    confidence=KnowledgeCompiler._safe_float(
                        item.get("confidence", 0.5)
                    ),
                    relation_to_existing=str(
                        item.get("relation_to_existing", "new")
                    ).strip(),
                    existing_entity_id=item.get("existing_entity_id"),
                )
            )
        return entities

    @staticmethod
    def _parse_relation_response(
        response: str, entity_map: dict[str, str]
    ) -> list[RawRelation]:
        """Parse LLM JSON response into RawRelation list."""
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text)
            if not isinstance(data, list):
                data = [data]
        except json.JSONDecodeError as exc:
            del exc
            import re

            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError as exc:
                    del exc
                    return []
            else:
                return []

        relations = []
        valid_names = {n.lower() for n in entity_map}
        for item in data:
            if not isinstance(item, dict):
                continue
            subj = str(item.get("subject", "")).strip()
            obj = str(item.get("object", "")).strip()
            # Only accept relations where both entities exist in our map
            if subj.lower() not in valid_names and subj not in entity_map:
                continue
            if obj.lower() not in valid_names and obj not in entity_map:
                continue
            relations.append(
                RawRelation(
                    subject=subj,
                    relation_type=str(item.get("relation_type", "related_to")).strip(),
                    object=obj,
                    description=str(item.get("description", "")).strip(),
                    confidence=KnowledgeCompiler._safe_float(
                        item.get("confidence", 0.5)
                    ),
                    source_chunk_ids=item.get("source_chunk_ids", []) or [],
                )
            )
        return relations

    @staticmethod
    def _fallback_entity_extraction(text: str) -> list[RawEntity]:
        """Simple keyword-based entity extraction when LLM is unavailable."""
        # This is a minimal fallback — extracts capitalized phrases and key terms
        import re

        entities: list[RawEntity] = []
        seen: set[str] = set()

        # Extract potential named entities: capitalized multi-word phrases
        patterns = [
            r"《([^》]+)》",  # Chinese book/article titles
            r'"([^"]{2,30})"',  # Quoted terms
            r"「([^」]{2,30})」",  # Japanese-style quotes
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})",  # English proper nouns
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if len(name) < 2 or name.lower() in seen:
                    continue
                seen.add(name.lower())
                entities.append(
                    RawEntity(
                        canonical_name=name,
                        entity_type="concept",
                        description=name,
                        confidence=0.3,
                    )
                )

        return entities[:20]
