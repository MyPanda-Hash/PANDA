"""把周期报表变成一份能直接用的 PPTX。

为什么做：报表此前只是一段 Markdown 文本，看起来像日志。
企业里日报/周报是要拿去过会的，需要「有设计感的版式」而不是纯文本。

做法：不重写版式引擎，复用 presentation_deck 的内置模板，
把报表的每个小节当成一页的内容来源。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services import assistant_reports, presentation_deck

KIND_LABEL = {"daily": "日报", "weekly": "周报", "monthly": "月报"}


def _split_chapters(markdown: str) -> list[tuple[str, str]]:
    """把报表正文按 `### ` 小节切开，每个小节成为一页。

    报表是 `render_report()` 产出的固定结构（概览/新增分布/已处理/…），
    所以按三级标题切就够，不需要通用 Markdown 解析。
    """
    chapters: list[tuple[str, str]] = []
    title = ""
    buffer: list[str] = []

    def flush() -> None:
        if title and any(line.strip() for line in buffer):
            chapters.append((title, chr(10).join(buffer).strip()))

    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            flush()
            title = stripped[4:].strip()
            buffer = []
            continue
        if stripped.startswith("## "):
            # 一级小节（报表总标题）不进正文页
            continue
        if title:
            buffer.append(line)
    flush()

    if not chapters:
        chapters.append(("概览", (markdown or "").strip() or "本期没有记录。"))
    return chapters[:20]


def _session_body(db: Session, session_id: str) -> str:
    # 取该会话最后一条 assistant 消息（就是报表正文）
    if not (session_id or "").strip():
        return ""
    try:
        from sqlalchemy import select
        from app.models.chat import ChatMessage
        rows = list(
            db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id.strip())
                .where(ChatMessage.role == "assistant")
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
        )
    except Exception:
        return ""
    if not rows:
        return ""
    return str(rows[0].content or "")


def build_pptx(
    db: Session,
    *,
    kind: str,
    anchor: date | None = None,
    session_id: str = "",
) -> tuple[bytes, str]:
    """生成一份报表 PPTX，返回 (文件字节, 标题)。"""
    if kind not in KIND_LABEL:
        raise ValueError("不支持的报表类型：" + str(kind))

    ctx = assistant_reports.build_context(db, kind=kind, anchor=anchor)
    title = assistant_reports._report_title(kind, ctx)

    # 正文优先取「对话里那份」：用户看到的除了系统口径的数字，
    # 还有模型追加的「分析」「建议」两节，PPT 要和它保持一致。
    body = _session_body(db, session_id) or assistant_reports.render_report(ctx)

    start = ctx.period_start.strftime("%Y-%m-%d")
    end = ctx.period_end.strftime("%Y-%m-%d")
    audience = "生产管理团队 · " + KIND_LABEL[kind] + " · " + start + " ~ " + end

    spec = presentation_deck.build_deck_spec(
        title,
        _split_chapters(body),
        audience=audience,
    )
    _restyle(spec, kind=kind, start=start, end=end)
    return presentation_deck.render_deck_spec(spec), title


def _restyle(spec: dict, *, kind: str, start: str, end: str) -> None:
    """把「方案汇报」口径的封面/结尾改成本报表的口径。

    presentation_deck 本是给项目汇报用的，默认文案是
    「基于引用资料整理的方案汇报」和「谢谢 / 下一步：确认范围、责任人与验收口径」，
    放进日报里前言不搭后语，必须换掉。
    """
    slides = spec.get("slides") or []
    if not slides:
        return
    cover = slides[0]
    cover["takeaway"] = KIND_LABEL[kind] + " · " + start + " ~ " + end
    # 封面左下角抬头：模板默认是"PROPOSAL"（提案），日报不能这么写
    cover["brand_kicker"] = "PRODUCTION / MES " + KIND_LABEL[kind].upper()
    cover["speaker_notes"] = ""

    for slide in slides[1:]:
        if str(slide.get("role") or "").lower() == "closing":
            slide["title"] = "下一步"
            slide["takeaway"] = "按「建议」逐条落人落期，下个周期复核"
            slide["speaker_notes"] = ""


def filename_of(kind: str, title: str) -> str:
    """下载文件名：中文可用，去掉路径分隔符。"""
    safe = (title or KIND_LABEL.get(kind, "报表")).replace("/", "-").replace(chr(92), "-")
    return safe + ".pptx"