"""Canonical writing-skill definitions shared by the writing provider and
the Pi-runtime agent prompt.

Single source of truth for business writing skills. Keeps the backend
guidance, the chat-facing skill presentation, and the structured output
contract in one place so the writing workspace and 翠花 chat cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WritingFormat = Literal["docx", "pptx"]

# Deck page roles understood by the presentation renderer. The model picks
# one per slide; the renderer owns the actual layout, never the model.
DeckRole = Literal[
    "cover",
    "section",
    "problem",
    "objective",
    "solution",
    "architecture",
    "process",
    "timeline",
    "metric",
    "comparison",
    "case",
    "risk",
    "acceptance",
    "closing",
]


@dataclass(frozen=True, slots=True)
class WritingSkillDefinition:
    key: str
    label: str
    group: str
    description: str
    focus: str
    quality_checks: tuple[str, ...]
    supported_formats: tuple[WritingFormat, ...] = ("docx",)
    presentation_roles: tuple[DeckRole, ...] = ()


WRITING_SKILLS: dict[str, WritingSkillDefinition] = {
    "bid": WritingSkillDefinition(
        key="bid",
        label="写标书",
        group="文稿写作",
        description="基于引用招标资料生成逐项响应、可核验的投标文件",
        focus=(
            "逐项响应招标要求和评分项，建立要求、响应、证据、缺口矩阵，"
            "优先识别资格与废标风险。"
        ),
        quality_checks=(
            "资格条件",
            "评分项覆盖",
            "技术与商务偏离",
            "承诺和数字有证据",
            "待补充项",
        ),
        supported_formats=("docx",),
    ),
    "proposal": WritingSkillDefinition(
        key="proposal",
        label="写方案",
        group="文稿写作",
        description="基于引用需求和技术资料生成可落地、可验收的项目方案",
        focus=(
            "把引用资料转成可落地、可执行、可验收的方案，明确范围、架构、"
            "阶段、责任、风险和验收标准。"
        ),
        quality_checks=(
            "范围边界",
            "方案取舍",
            "实施里程碑",
            "责任分工",
            "验收标准",
            "证据完整性",
        ),
        supported_formats=("docx", "pptx"),
        presentation_roles=(
            "cover",
            "problem",
            "objective",
            "solution",
            "architecture",
            "process",
            "timeline",
            "risk",
            "acceptance",
            "closing",
        ),
    ),
    "quote": WritingSkillDefinition(
        key="quote",
        label="写报价",
        group="文稿写作",
        description="基于引用范围和商务资料生成可核算、可谈判的报价文件",
        focus=(
            "锁定服务范围和商务假设，输出可复核的明细、公式、税费、付款、"
            "有效期及范围外条件。"
        ),
        quality_checks=(
            "数量和单位",
            "单价与合计",
            "税费和币种",
            "交付与付款",
            "待确认假设",
        ),
        supported_formats=("docx",),
    ),
    "report": WritingSkillDefinition(
        key="report",
        label="写汇报材料",
        group="文稿写作",
        description="根据现有业务资料，撰写一份面向管理层的汇报材料",
        focus=(
            "面向决策者说明现状、关键发现、影响、方案选择与建议动作；"
            "结论先行，事实与判断分离，数字可追溯。"
        ),
        quality_checks=(
            "执行摘要",
            "关键发现",
            "数字有证据",
            "影响与建议",
            "待决策事项",
        ),
        supported_formats=("docx", "pptx"),
        presentation_roles=(
            "cover",
            "metric",
            "problem",
            "comparison",
            "timeline",
            "risk",
            "acceptance",
            "closing",
        ),
    ),
}


def writing_skill_definition(key: str | None) -> WritingSkillDefinition | None:
    normalized = (key or "").strip().lower()
    return WRITING_SKILLS.get(normalized)


def writing_skill_payload(key: str | None) -> dict:
    """Build the ``writing_skill`` object embedded in Pi prompts."""
    definition = writing_skill_definition(key)
    if definition is None:
        return {
            "key": (key or "other").strip(),
            "label": "业务文稿",
            "focus": "",
            "quality_checks": [],
            "supported_formats": ["docx"],
            "presentation_roles": [],
        }
    return {
        "key": definition.key,
        "label": definition.label,
        "focus": definition.focus,
        "quality_checks": list(definition.quality_checks),
        "supported_formats": list(definition.supported_formats),
        "presentation_roles": list(definition.presentation_roles),
    }


def writing_skill_presentation(key: str | None) -> dict:
    """Chat-facing skill metadata for the /chat/capabilities endpoint."""
    definition = writing_skill_definition(key)
    if definition is None:
        return {}
    return {
        "label": definition.label,
        "group": definition.group,
        "description": definition.description,
    }
