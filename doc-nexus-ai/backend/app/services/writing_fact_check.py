"""Deterministic claim/support checks against cited evidence excerpts."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from app.services.evidence_snapshots import parse_citation_id, read_snapshot

_MARKER_RE = re.compile(
    r"\^?\[(?:citation:)?(?P<id>(?:writing-evidence|vector-evidence):[^\]\r\n]+)\]"
    r"|\^?\[(?P<legacy>citation:[^\]\r\n]+)\]"
)
# 展示型引用仅豁免末尾 #序号；保留标签正文的业务数值，不解析为绑定 ID。
# 普通 Markdown 链接 [文字#数字](地址) 与嵌套链接（后随 ]）不属于展示型引用。
_DISPLAY_CITATION_RE = re.compile(r"(\^?\[[^\[\]#\r\n]*)#\d+\](?![\](])")
# 仅明确的 2D/3D 维度术语不计数量；小写 d 可能是天数、`2D-3` 可能是型号，仍保留核验。
_DIMENSION_RE = re.compile(r"(?<![A-Za-z0-9_.])[23]D(?![A-Za-z0-9_])(?!-\d)")
_FENCED_CODE_RE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*(?:\n.*?(?:^[ \t]*\1[ \t]*$|\Z)|\Z)")
_INLINE_CODE_RE = re.compile(r"(`+).*?\1")
_LIST_ORDINAL_RE = re.compile(
    r"(?m)^(\s*(?:>\s*)?(?:[-+*]\s+)?)(?:\d+[.)]\s+|\d+）\s*|(?:\d+|[一二三四五六七八九十百]+)、\s*|[（(]\d+[）)]\s*)"
)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_FIELD_NAMES = {
    "软件名称": "软件名称", "软件全称": "软件名称",
    "著作权人": "著作权人", "登记号": "登记号",
    "专利名称": "专利名称", "发明名称": "专利名称",
    "专利号": "专利号", "专利申请号": "专利号",
    "专利权人": "专利权人",
}
_FIELD_PATTERN = "|".join(sorted(_FIELD_NAMES, key=len, reverse=True))
_LABELED_FIELD_RE = re.compile(
    rf"(?P<field>{_FIELD_PATTERN})\s*[:：]\s*(?P<value>.*?)"
    rf"(?=\s*(?:{_FIELD_PATTERN})\s*[:：]|[\r\n|；;]|$)",
    re.MULTILINE,
)


def _field_value(text: str) -> str:
    return re.sub(r"\s+", "", _MARKER_RE.sub("", text)).strip("*_`：:；;。 ")


def _field_rows(markdown: str):
    for paragraph in re.split(r"\n\s*\n", _without_code(markdown)):
        lines = paragraph.splitlines() if "|" in paragraph else [paragraph]
        headers: dict[int, str] = {}
        for index, line in enumerate(lines):
            plain = _MARKER_RE.sub("", line)
            cells = plain.strip().strip("|").split("|")
            if index + 1 < len(lines) and _TABLE_SEPARATOR_RE.fullmatch(_MARKER_RE.sub("", lines[index + 1])):
                headers = {
                    column: _FIELD_NAMES[name]
                    for column, cell in enumerate(cells)
                    if (name := _field_value(cell)) in _FIELD_NAMES
                }
                continue
            if _TABLE_SEPARATOR_RE.fullmatch(plain):
                continue
            fields = [
                (_FIELD_NAMES[match.group("field")], _field_value(match.group("value")))
                for match in _LABELED_FIELD_RE.finditer(plain)
                if _field_value(match.group("value"))
            ]
            fields.extend(
                (field, _field_value(cells[column]))
                for column, field in headers.items()
                if column < len(cells) and _field_value(cells[column])
            )
            yield line, fields


def _without_code(text: str) -> str:
    return _INLINE_CODE_RE.sub("", _FENCED_CODE_RE.sub("", text or ""))


def inline_citation_ids(markdown: str) -> list[str]:
    """Return exact inline bindings, never model-declared reference positions."""
    return list(dict.fromkeys(
        str(match.group("id") or match.group("legacy"))
        for match in _MARKER_RE.finditer(_without_code(markdown))
    ))

# 标题行（含标题序号），必须先去标题再删列表前缀，否则「### 1、标题」会把 1 当事实数字。
_HEADING_RE = re.compile(
    r"(?m)^\s*#{1,6}\s+"
    r"(?:(?:\d+(?:\.\d+)+\s+)|\d+(?:\.\d+)+$|"
    r"(?:\d+(?:\.\d+)*[.、]\s*)|\d{1,2}(?=\s+))?"
)
# 明确的页面/里程碑/来源标识属于结构元数据，不应被当作企业事实核验。
_STRUCTURAL_LABEL_RE = re.compile(
    r"(?ix)"
    r"(?:(?<![A-Za-z0-9_])slide\s*\d+(?:\s*[-—–/]\s*\d+)?"
    r"|(?<![A-Za-z0-9_])(?:P|D|M|G|A|B|C)\s*\d+(?:\s*[-—–/]\s*\d+)?"
    r"|(?<![A-Za-z0-9_])T\s*\+\s*\d+(?:\s*[-—~至]\s*T?\s*\+\s*\d+)?"
    r"|(?<![A-Za-z0-9_])v\d+"
    r"|(?:取舍|风险|阶段|第|片段|版本|来源)\s*\#?\s*\d+(?!\d)(?!\s*[-—–~至]\s*\d+)"
    r"(?:\s*/\s*\d+)?)"
)
_STRUCTURAL_SECTION_RE = re.compile(
    r"(?<![\dA-Za-z_.])\d+(?:\.\d+)+"
    r"(?:\s*[-—–~至]\s*\d+(?:\.\d+)+)?"
    r"(?![A-Za-z])"
    r"(?!\s*(?:万|亿|元|倍|度|级|层|项|条|页|张|秒|分钟|小时|工作日|个百分点))"
    r"(?=\s*(?:[、，,。；;：:\)\]）|+&/]|$|[\u4e00-\u9fff]|"
    r"[A-Z](?:\s*[-—–]\s*[A-Z])?))"
)
_INLINE_ORDINAL_RE = re.compile(
    r"(?<![\d.])(?:\d+[.)]|[一二三四五六七八九十百]+、)(?=\s+)"
)
_SOURCE_RANGE_RE = re.compile(
    r"\[\s*0\s*[-—–]\s*\d+\s*\]"
    r"|(?:字符|字节|行|片段)\s*(?:区间|范围)\s*(?:为|:|：)?\s*\d+\s*[-—–]\s*\d+"
    r"|(?<![\d.])\d+\s*[-—–]\s*\d+(?=\s*(?:字符|字节|行)?\s*(?:区间|范围))"
)
_LAYOUT_TOKEN_RE = re.compile(
    r"(?ix)(?:"
    r"(?<![\d.])16\s*:\s*9(?!\d)"
    r"|(?<![\d.])\d+(?:\.\d+)?\s*(?:pt|px|dpi|页|张指标卡)(?![A-Za-z])"
    r"|(?:标题层级|正文字号)\s*(?:不超过|不小于|≤|≥)\s*\d+(?:\.\d+)?\s*(?:级|pt)"
    r"|(?:注意力窗口\s*(?:为|只有|约)?\s*\d+\s*[-—–]\s*\d+\s*秒"
    r"|\d+\s*[-—–]\s*\d+\s*秒\s*的注意力窗口)"
    r")"
)
_STRUCTURAL_COUNT_RE = re.compile(
    r"(?ix)(?:"
    r"(?:共|不超过|至少|至多|限于|覆盖|含)\s*\d+\s*"
    r"(?:条(?:问答|可判定项|预设问答|可核验条目)?|类(?:活动|角色)?|"
    r"项(?:取舍|验收|检查|条目)?|份\s*文档|个\s*(?:片段|工作日)|页(?:数)?|层|工作日)"
    r"|(?:给出|提供|将)\s*\d+\s*"
    r"(?:条(?:问答|可判定项|预设问答|可核验条目)|类(?:活动|角色)|"
    r"项(?:取舍|验收|检查|条目)|份\s*文档|个\s*片段|个\s*工作日)"
    r"|仅\s*\d+\s*(?:份\s*文档|个\s*片段|个\s*工作日)"
    r"|\d+\s*(?:个片段|份文档|个工作日|工作日)"
    r"|占位数量必须为\s*\d+"
    r"|(?:≤|≥|不少于|不小于)\s*\d+\s*条"
    r")"
)
_QUALITY_PERCENT_RE = re.compile(
    r"(?:(?:缺口|全部事实性陈述|每条(?:页面陈述)?|覆盖率|完成率|"
    r"验收(?:标准)?|条目)\s*)\d+\s*[%％]"
    r"(?=\s*(?:登记|可追溯|覆盖|完成|通过|一致|留痕))"
)


def _strip_structural_tokens(text: str) -> str:
    for pattern in (
        _SOURCE_RANGE_RE,
        _LAYOUT_TOKEN_RE,
        _STRUCTURAL_COUNT_RE,
        _QUALITY_PERCENT_RE,
        _STRUCTURAL_LABEL_RE,
        _STRUCTURAL_SECTION_RE,
        _INLINE_ORDINAL_RE,
    ):
        text = pattern.sub(" ", text)
    return text


# 数字 token 只取完整数字本身（含千分位、小数与百分号），单位/后续字母只被消费不进入 token：
# "2.4G" 得到 "2.4"、"12,000.50" 得到 "12000.50"，不允许回溯把完整数字截短。
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<percent>[%％])?"
    r"[A-Za-z]*(?![A-Za-z0-9_])(?!\.\d)"
)
_CN_AMOUNT_RE = re.compile(
    r"(?=[零一二三四五六七八九十百千万亿]*[十百千万亿])"
    r"[零一二三四五六七八九十百千万亿]{2,}"
    r"(?:点[零一二三四五六七八九]+)?(?:百分之?)?"
)
# 完整识别日期并占用整段 span：年月日组件不再作为独立数字重复核对。
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2}|19\d{2})\s*(?:年|[./-])\s*(?P<month>0?[1-9]|1[0-2])(?!\d)"
    r"(?:\s*(?:月|[./-])\s*(?P<day>0?[1-9]|[12]\d|3[01])(?!\d))?\s*日?"
)
_INJECTION_RE = re.compile(
    r"(忽略(?:前述|以上|之前)?(?:要求|指令|规则)|ignore\s+(all\s+)?previous|"
    r"disregard\s+(all\s+)?prior|system\s*prompt|你现在是|jailbreak)",
    re.IGNORECASE,
)


def _canonical_date(match: re.Match[str]) -> str | None:
    """把合法日期统一成可读的 YYYY-MM[-DD]；日历上不存在的日期不规范化。"""
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = match.group("day")
    if day is None:
        return f"{year:04d}-{month:02d}"
    try:
        value = date(year, month, int(day))
    except ValueError:
        return None
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


def extract_fact_tokens(text: str, *, strip_structural: bool = True) -> set[str]:
    text = _MARKER_RE.sub("", _without_code(text))
    text = _DISPLAY_CITATION_RE.sub(r"\1]", text)
    text = _DIMENSION_RE.sub(" ", text)
    text = _HEADING_RE.sub("", text)
    text = _LIST_ORDINAL_RE.sub(r"\1", text)
    if strip_structural:
        text = _strip_structural_tokens(text)
    values: set[str] = set()
    remainder: list[str] = []
    cursor = 0
    for match in _DATE_RE.finditer(text):
        token = _canonical_date(match)
        remainder.append(text[cursor:match.start()])
        cursor = match.end()
        if token is None:
            # 日历上不存在的日期不算日期，年月日仍按普通数字核对。
            remainder.append(match.group(0))
        else:
            values.add(token)
            remainder.append(" ")
    remainder.append(text[cursor:])
    for match in _NUMBER_RE.finditer("".join(remainder)):
        token = match.group("value").replace(",", "")
        if token:
            values.add(token + ("%" if match.group("percent") else ""))
    values.update(match.group(0) for match in _CN_AMOUNT_RE.finditer(text))
    values.discard("")
    return values


_TITLE_RE = re.compile(r"《([^《》\r\n]{1,120})》")


def _code_line_flags(markdown: str) -> list[bool]:
    """标记围栏代码块覆盖的原始行，便于跳过后仍保留准确行号。"""
    total = markdown.count("\n") + 1
    flags = [False] * total
    for match in _FENCED_CODE_RE.finditer(markdown):
        first = markdown.count("\n", 0, match.start())
        last = markdown.count("\n", 0, match.end())
        for index in range(first, min(last + 1, total)):
            flags[index] = True
    return flags


def _block_lines(start: int, paragraph: str) -> list[tuple[int, str]]:
    if "|" not in paragraph:
        return [(start, paragraph)]
    return [
        (start + offset, line)
        for offset, line in enumerate(paragraph.split("\n"))
        if line.strip()
    ]


def _fact_blocks(markdown: str) -> list[tuple[int, str]]:
    """按空行切段（表格内逐行），返回 (1-based 起始行号, 块文本)。"""
    text = markdown or ""
    code_lines = _code_line_flags(text)
    blocks: list[tuple[int, str]] = []
    paragraph: list[str] = []
    start = 0
    for index, raw in enumerate(text.split("\n")):
        line = _INLINE_CODE_RE.sub("", raw)
        if code_lines[index] or not line.strip():
            if paragraph:
                blocks.extend(_block_lines(start, "\n".join(paragraph)))
                paragraph = []
            continue
        if not paragraph:
            start = index + 1
        paragraph.append(line)
    if paragraph:
        blocks.extend(_block_lines(start, "\n".join(paragraph)))
    return blocks


def _verified_titles(citation_ids: list[str], evidence_by_id: dict[str, dict[str, Any]]) -> set[str]:
    """可信标题只来自已成功验证的快照条目，不采信普通 document_name/model 字段。"""
    titles: set[str] = set()
    for citation_id in citation_ids:
        item = evidence_by_id.get(citation_id)
        if not isinstance(item, dict) or item.get("snapshot_status") != "verified":
            continue
        name = item.get("verified_document_name")
        if isinstance(name, str) and name.strip():
            titles.add(name.strip())
    return titles


def _exempt_verified_titles(text: str, titles: set[str]) -> str:
    """仅精确《标题》且该标题已由同段引用验证时才排除标题内数字。"""
    if not titles:
        return text
    return _TITLE_RE.sub(
        lambda match: "" if match.group(1).strip() in titles else match.group(0), text
    )


def _issue_text(block: str) -> str:
    return re.sub(r"\s+", " ", _MARKER_RE.sub("", block)).strip()[:240]


def _block_has_facts(block: str, tokens: set[str]) -> bool:
    if tokens:
        return True
    plain = _MARKER_RE.sub("", block)
    if _LABELED_FIELD_RE.search(plain):
        return True
    return False


def check_claim_support(
    markdown: str,
    citation_ids: list[str],
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    bound_ids = inline_citation_ids(markdown)
    unknown: list[str] = []
    corrupt: list[str] = []
    unverified: list[str] = []
    evidence_tokens: dict[str, set[str]] = {}
    evidence_fields: dict[str, dict[str, set[str]]] = {}
    snapshots: dict[tuple[str, str], str | None] = {}
    misplaced = [
        citation_id
        for line in _without_code(markdown).splitlines()
        if _TABLE_SEPARATOR_RE.fullmatch(_MARKER_RE.sub("", line))
        for citation_id in inline_citation_ids(line)
    ]
    for citation_id in bound_ids:
        item = evidence_by_id.get(citation_id)
        if item is None:
            unknown.append(citation_id)
            continue
        parsed = parse_citation_id(citation_id)
        if parsed is None:
            corrupt.append(citation_id)
            continue
        version_id, digest, start, end = parsed
        if digest is None:
            # Historical offsets do not establish a binding to today's text.
            unverified.append(citation_id)
            continue
        if (
            item.get("document_version_id") != version_id
            or item.get("text_sha256") != digest
            or item.get("char_start") != start
            or item.get("char_end") != end
        ):
            corrupt.append(citation_id)
            continue
        key = (version_id, digest)
        if key not in snapshots:
            snapshots[key] = read_snapshot(version_id, digest)
        snapshot = snapshots[key]
        if snapshot is None or not 0 <= start < end <= len(snapshot):
            corrupt.append(citation_id)
            continue
        evidence_tokens[citation_id] = extract_fact_tokens(
            snapshot[start:end],
            strip_structural=False,
        )
        source_fields: dict[str, set[str]] = {}
        for _, fields in _field_rows(snapshot[start:end]):
            for field, value in fields:
                source_fields.setdefault(field, set()).add(value)
        evidence_fields[citation_id] = source_fields

    claimed = extract_fact_tokens(markdown)
    unsupported: set[str] = set()
    support_issues: list[dict[str, Any]] = []
    # A reference on one table row/paragraph cannot support another one.
    for line_number, block in _fact_blocks(markdown):
        if _TABLE_SEPARATOR_RE.fullmatch(_MARKER_RE.sub("", block)):
            continue
        ids = inline_citation_ids(block)
        tokens = extract_fact_tokens(
            _exempt_verified_titles(
                block,
                _verified_titles([cid for cid in ids if cid in evidence_tokens], evidence_by_id),
            )
        )
        cited_tokens: set[str] = set()
        for citation_id in ids:
            cited_tokens.update(evidence_tokens.get(citation_id, ()))
        missing = tokens - cited_tokens
        unsupported.update(missing)
        if not ids:
            # 只有含数字/日期的实质段落才产生定位信息，纯叙述不阻断。
            if _block_has_facts(block, tokens):
                support_issues.append({
                    "line_number": line_number,
                    "text": _issue_text(block),
                    "reason": "missing_inline_binding",
                    "unsupported_tokens": sorted(tokens),
                    "citation_ids": [],
                })
        elif missing:
            support_issues.append({
                "line_number": line_number,
                "text": _issue_text(block),
                "reason": "unsupported_fact",
                "unsupported_tokens": sorted(missing),
                "citation_ids": ids,
            })
    unsupported_fields: list[dict[str, Any]] = []
    uncertain_fields: list[dict[str, Any]] = []
    records_by_citations: dict[tuple[str, ...], dict[tuple[str, str], dict[str, Any]]] = {}
    for block, fields in _field_rows(markdown):
        ids = inline_citation_ids(block)
        identifiers = {
            _record_key(identifier): identifier
            for match in _RECORD_IDENTIFIER_RE.finditer(_MARKER_RE.sub("", block))
            if (identifier := re.sub(r"\s+", "", match.group(1) or match.group(0)).upper().replace("．", "."))
        }
        row_records: dict[tuple[str, str], dict[str, Any]] = {}
        if identifiers:
            # Build the ledger from this row's verified spans only: field values
            # from another retrieved span must not leak through a merged record.
            cited = tuple(sorted(citation_id for citation_id in ids if citation_id in evidence_tokens))
            if cited not in records_by_citations:
                records_by_citations[cited] = {
                    _record_key(record["identifier"]): record
                    for record in inventory_records({
                        "evidence": [evidence_by_id[citation_id] for citation_id in cited],
                    })
                }
            row_records = records_by_citations[cited]
            for key, identifier in identifiers.items():
                if key not in row_records:
                    unsupported_fields.append({
                        "field": "登记号" if key[0] == "copyright_registration" else "专利号",
                        "value": identifier, "citation_ids": ids,
                        "reason": "identifier_not_in_cited_record",
                    })
            if len(identifiers) > 1:
                # Flat row fields do not encode which identifier owns which
                # value. Do not accept an arbitrary union as correspondence.
                for field, value in fields or [
                    ("登记号" if key[0] == "copyright_registration" else "专利号", identifier)
                    for key, identifier in identifiers.items()
                ]:
                    uncertain_fields.append({
                        "field": field, "value": value, "citation_ids": ids,
                        "reason": "ambiguous_record_correspondence",
                    })
                continue
        for field, value in fields:
            source_values: set[str] = set()
            if identifiers:
                record = row_records.get(next(iter(identifiers)))
                if record is not None:
                    source_values.update(record.get("field_values", {}).get(field, ()))
            else:
                for citation_id in ids:
                    source_values.update(evidence_fields.get(citation_id, {}).get(field, ()))
            if not any(_record_field_matches(field, expected, [(field, value)]) for expected in source_values):
                unsupported_fields.append({
                    "field": field, "value": value, "citation_ids": ids,
                    "reason": "value_not_in_cited_field" if source_values else "cited_field_unavailable",
                })
            elif len(source_values) > 1:
                uncertain_fields.append({
                    "field": field, "value": value, "citation_ids": ids,
                    "reason": "ambiguous_source_field",
                })
    findings: list[str] = []
    if markdown.strip() and not bound_ids:
        findings.append("正文缺少行内证据绑定；检索参考资料不等于正文已引用")
    if unknown:
        findings.append(f"未知或未授权引用: {', '.join(unknown[:10])}")
    if corrupt:
        findings.append(f"引用快照或字符区间无效: {', '.join(corrupt[:10])}")
    if unverified:
        findings.append("历史引用缺少不可变文本绑定，需要重新核验")
    if misplaced:
        findings.append("表格分隔线不能承载事实引用")
    if unsupported:
        findings.append(
            "正文含有未被同段引用片段支持的数字/日期: "
            + ", ".join(sorted(unsupported)[:20])
        )
    if unsupported_fields:
        findings.append("证书字段未被同一行/段的引用支持：" + "、".join(
            f"{item['field']}={item['value']}" for item in unsupported_fields[:20]
        ))
    if uncertain_fields:
        findings.append("引用中存在多个可能的证书字段值，请核对逐条对应关系")
    structural_errors = [
        *({"code": "unknown_citation", "citation_id": item} for item in unknown),
        *({"code": "corrupt_citation", "citation_id": item} for item in corrupt),
        *({"code": "misplaced_citation", "citation_id": item} for item in misplaced),
    ]
    return {
        "passed": not findings,
        "structural_valid": not structural_errors,
        "structural_errors": structural_errors,
        "content_uncertain": bool(unsupported or unverified or not bound_ids or unsupported_fields or uncertain_fields),
        "semantic_support": "unverified",
        "unsupported_fields": unsupported_fields,
        "uncertain_fields": uncertain_fields,
        "findings": findings,
        "claimed_tokens": sorted(claimed)[:100],
        "unsupported_tokens": sorted(unsupported)[:100],
        "support_issues": support_issues,
        "unknown_citations": unknown,
        "corrupt_citations": corrupt,
        "unverified_citations": unverified,
        "cited_citation_ids": bound_ids,
        "retrieved_citation_ids": list(evidence_by_id),
        "unbound_citation_ids": [item for item in citation_ids if item not in bound_ids],
    }


def support_content_blocker(checked: dict[str, Any]) -> dict[str, Any] | None:
    """生成草稿与导出共用的确定性内容阻断规则。"""
    if not any(checked.get(key) for key in (
        "unsupported_tokens", "unsupported_fields", "uncertain_fields",
    )):
        return None
    return {
        "code": "missing_inline_bindings" if not checked.get("cited_citation_ids") else "unsupported_content",
        "unsupported_tokens": checked.get("unsupported_tokens") or [],
        "unsupported_fields": checked.get("unsupported_fields") or [],
        "uncertain_fields": checked.get("uncertain_fields") or [],
        "support_issues": checked.get("support_issues") or [],
        "findings": checked.get("findings") or [],
    }


def citation_binding_only_changed(before: str, after: str) -> bool:
    """只有引用绑定或空白调整才允许免于正文变更确认。"""
    return re.sub(r"\s+", "", _MARKER_RE.sub("", before)) == re.sub(
        r"\s+", "", _MARKER_RE.sub("", after)
    )


_RECORD_IDENTIFIER_RE = re.compile(
    r"(?<![A-Z0-9])(?:20\d{2}SR\d{4,}|ZL\s*\d{8,13}(?:[.．][0-9X])?)(?![A-Z0-9])"
    r"|(?:专利号|专利申请号)\s*[:：]?\s*((?:CN\s*)?\d{8,13}(?:[.．][0-9X])?)",
    re.IGNORECASE,
)


def _record_key(identifier: str) -> tuple[str, str]:
    if re.fullmatch(r"20\d{2}SR\d{4,}", identifier):
        return "copyright_registration", identifier
    return "patent_number", identifier.removeprefix("ZL").removeprefix("CN")


def _record_field_matches(field_name: str, expected: str, fields: list[tuple[str, str]]) -> bool:
    return any(
        name == field_name and (
            _record_key(value.upper()) == _record_key(expected.upper())
            if field_name in {"登记号", "专利号"} else value == expected
        )
        for name, value in fields
    )


def inventory_records(evidence_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Recognize explicit source identifiers, not arbitrary semantic records.

    Callers authorize the evidence first. Only delivered immutable ranges
    contribute records; a coverage gap remains a gap even if the file exists.
    """
    groups: dict[tuple[str, str, str], list[tuple[int, int, str]]] = {}
    for item in evidence_payload.get("evidence", []):
        if not isinstance(item, dict):
            continue
        citation_id = str(item.get("id") or "")
        parsed = parse_citation_id(citation_id)
        document_id = item.get("document_id")
        if parsed is None or parsed[1] is None or not isinstance(document_id, str):
            continue
        version, digest, start, end = parsed
        assert digest is not None
        if (
            item.get("document_version_id") != version
            or item.get("text_sha256") != digest
            or item.get("char_start") != start
            or item.get("char_end") != end
        ):
            continue
        groups.setdefault((document_id, version, digest), []).append((start, end, citation_id))
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for (document_id, version, digest), spans in sorted(groups.items()):
        text = read_snapshot(version, digest)
        if text is None:
            continue
        spans.sort()
        delivered: list[list[int]] = []
        for start, end, _ in spans:
            if not 0 <= start < end <= len(text):
                continue
            if delivered and start <= delivered[-1][1]:
                delivered[-1][1] = max(delivered[-1][1], end)
            else:
                delivered.append([start, end])
        for start, end in delivered:
            for match in _RECORD_IDENTIFIER_RE.finditer(text, start, end):
                identifier = re.sub(r"\s+", "", match.group(1) or match.group(0)).upper().replace("．", ".")
                key = _record_key(identifier)
                record = records.setdefault(key, {
                    "record_ref": "writing-record:" + hashlib.sha256(
                        f"{key[0]}:{key[1]}".encode()
                    ).hexdigest(),
                    "document_id": document_id,
                    "identifier": identifier,
                    "identifier_type": key[0],
                    "business_identifier": key[1],
                    "supporting_document_ids": [],
                    "evidence_ids": [],
                    "field_values": {},
                    "field_evidence": {},
                })
                record["supporting_document_ids"] = sorted(
                    set(record["supporting_document_ids"]) | {document_id}
                )
                record["evidence_ids"] = sorted(set(record["evidence_ids"]) | {
                    citation_id for span_start, span_end, citation_id in spans
                    if span_start < match.end() and span_end > match.start()
                })
                name_field = "软件名称" if key[0] == "copyright_registration" else "专利名称"
                names = [
                    field.start() for field in _LABELED_FIELD_RE.finditer(text, start, end)
                    if _FIELD_NAMES[field.group("field")] == name_field
                ]
                segment_start = max((position for position in names if position <= match.start()), default=start)
                segment_end = min((position for position in names if position > match.start()), default=end)
                segment_keys = {
                    _record_key(re.sub(r"\s+", "", found.group(1) or found.group(0)).upper().replace("．", "."))
                    for found in _RECORD_IDENTIFIER_RE.finditer(text, segment_start, segment_end)
                }
                if segment_keys == {key}:
                    for field in _LABELED_FIELD_RE.finditer(text, segment_start, segment_end):
                        field_name = _FIELD_NAMES[field.group("field")]
                        value = _field_value(field.group("value"))
                        if not value:
                            continue
                        record["field_values"][field_name] = sorted(
                            set(record["field_values"].get(field_name, [])) | {value}
                        )
                        record["field_evidence"][field_name] = sorted(
                            set(record["field_evidence"].get(field_name, [])) | {
                                citation_id for span_start, span_end, citation_id in spans
                                if span_start < field.end() and span_end > field.start()
                            }
                        )
    return sorted(records.values(), key=lambda row: (row["document_id"], row["identifier"]))


def reconcile_inventory(
    markdown: str,
    reconciliation: Any,
    evidence_payload: dict[str, Any],
) -> dict[str, Any]:
    """Check declared dispositions and recognized records against actual output.

    Relevance explanations remain model judgments, not semantic proof.
    An explicitly irrelevant source is not a missing citation.
    """
    coverage = evidence_payload.get("coverage") or {}
    manifest = coverage.get("documents") if isinstance(coverage, dict) else None
    expected = {
        row["document_id"] for row in (manifest or [])
        if isinstance(row, dict) and isinstance(row.get("document_id"), str)
    }
    source_records = inventory_records(evidence_payload)
    records_by_doc: dict[str, dict[str, dict[str, Any]]] = {}
    for record in source_records:
        for document_id in record["supporting_document_ids"]:
            records_by_doc.setdefault(document_id, {})[record["record_ref"]] = record
    evidence_docs: dict[str, str] = {}
    snapshots: dict[tuple[str, str], str | None] = {}
    for row in evidence_payload.get("evidence", []):
        if not isinstance(row, dict) or not isinstance(row.get("document_id"), str):
            continue
        citation_id = str(row.get("id") or "")
        parsed = parse_citation_id(citation_id)
        if parsed is None or parsed[1] is None:
            continue
        version, digest, start, end = parsed
        assert digest is not None
        if (
            row.get("document_version_id") != version or row.get("text_sha256") != digest
            or row.get("char_start") != start or row.get("char_end") != end
        ):
            continue
        key = (version, digest)
        if key not in snapshots:
            snapshots[key] = read_snapshot(version, digest)
        snapshot = snapshots[key]
        if snapshot is not None and 0 <= start < end <= len(snapshot):
            evidence_docs[citation_id] = row["document_id"]
    expected.update(
        row["document_id"] for row in evidence_payload.get("evidence", [])
        if isinstance(row, dict) and isinstance(row.get("document_id"), str)
    )
    raw = reconciliation if isinstance(reconciliation, dict) else {}
    documents = raw.get("documents")
    documents = documents if isinstance(documents, list) else []
    emitted = raw.get("emitted_record_refs")
    emitted = {item for item in emitted if isinstance(item, str)} if isinstance(emitted, list) else set()
    findings: list[dict[str, Any]] = []
    seen_docs: set[str] = set()
    relevant_records: set[str] = set()
    classified_records: set[str] = set()
    required_records: dict[str, set[str]] = {}
    reply_check = reconcile_inventory_reply(markdown, evidence_payload)
    missing_rendered_refs = {row["record_ref"] for row in reply_check["missing_records"]}
    known_records = {row["record_ref"] for row in source_records}

    def issue(code: str, document_id: str = "", record_ref: str = "") -> None:
        findings.append({"code": code, "document_id": document_id, "record_ref": record_ref})

    def bound(ids: Any, document_id: str) -> bool:
        return isinstance(ids, list) and bool(ids) and all(
            isinstance(item, str) and evidence_docs.get(item) == document_id for item in ids
        )

    if not isinstance(manifest, list):
        issue("inventory_manifest_unavailable")
    if isinstance(coverage, dict) and int(coverage.get("expected") or 0) > len(expected):
        issue("inventory_manifest_incomplete")
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        issue("inventory_text_coverage_incomplete")
    for row in documents:
        if not isinstance(row, dict):
            issue("inventory_invalid_disposition")
            continue
        document_id = str(row.get("document_id") or "")
        if document_id not in expected or document_id in seen_docs:
            issue("inventory_unknown_or_duplicate_document", document_id)
            continue
        seen_docs.add(document_id)
        disposition = row.get("disposition")
        if not isinstance(disposition, str) or disposition not in {"relevant", "irrelevant", "uncertain", "duplicate"}:
            issue("inventory_invalid_disposition", document_id)
            continue
        if disposition == "uncertain":
            issue("inventory_uncertain_document", document_id)
            continue
        if not str(row.get("reason") or "").strip() or not bound(row.get("evidence_ids"), document_id):
            issue("inventory_unbound_disposition_reason", document_id)
        if disposition == "irrelevant":
            continue
        expected_records = records_by_doc.get(document_id, {})
        required_records[document_id] = set(expected_records)
        if disposition == "duplicate":
            if not expected_records or any(
                len(record["supporting_document_ids"]) < 2 for record in expected_records.values()
            ):
                issue("inventory_unverified_duplicate_document", document_id)
            continue
        if not expected_records:
            issue("inventory_semantic_records_unverified", document_id)
        declared = row.get("records")
        declared = declared if isinstance(declared, list) else []
        seen_records: set[str] = set()
        for record in declared:
            if not isinstance(record, dict):
                issue("inventory_invalid_record", document_id)
                continue
            record_ref = str(record.get("record_ref") or "")
            source = expected_records.get(record_ref)
            if source is None or record_ref in seen_records:
                issue("inventory_unknown_or_duplicate_record", document_id, record_ref)
                continue
            seen_records.add(record_ref)
            classified_records.add(record_ref)
            state = record.get("disposition")
            if not isinstance(state, str) or state not in {"relevant", "irrelevant"}:
                issue("inventory_uncertain_record", document_id, record_ref)
                continue
            fields = record.get("field_evidence")
            if (
                not isinstance(fields, dict) or not fields
                or not all(bound(ids, document_id) for ids in fields.values())
                or not set(fields.get("identifier") or []).intersection(source["evidence_ids"])
            ):
                issue("inventory_unbound_record_fields", document_id, record_ref)
            if state == "irrelevant":
                if not str(record.get("reason") or "").strip():
                    issue("inventory_unbound_disposition_reason", document_id, record_ref)
                continue
            relevant_records.add(record_ref)
            if record_ref not in emitted or record_ref in missing_rendered_refs:
                issue("inventory_record_not_emitted", document_id, record_ref)
            matching_rows = [
                (block, row_fields) for block, row_fields in _field_rows(markdown)
                if _record_key(source["identifier"]) in {
                    _record_key(re.sub(r"\s+", "", match.group(1) or match.group(0)).upper().replace("．", "."))
                    for match in _RECORD_IDENTIFIER_RE.finditer(_MARKER_RE.sub("", block))
                }
                and set(inline_citation_ids(block)).intersection(source["evidence_ids"])
            ]
            for field_name, values in source.get("field_values", {}).items():
                if len(values) != 1:
                    issue("inventory_record_field_uncertain", document_id, record_ref)
                    continue
                field_ids = fields.get(field_name) if isinstance(fields, dict) else None
                if not bound(field_ids, document_id) or not set(field_ids).intersection(source["field_evidence"][field_name]):
                    issue("inventory_unbound_record_fields", document_id, record_ref)
                if not any(
                    _record_field_matches(field_name, values[0], row_fields)
                    and set(inline_citation_ids(block)).intersection(source["field_evidence"][field_name])
                    for block, row_fields in matching_rows
                ):
                    issue("inventory_record_field_not_emitted", document_id, record_ref)
    for document_id in expected - seen_docs:
        issue("inventory_missing_document_disposition", document_id)
    for document_id, record_refs in required_records.items():
        for record_ref in record_refs - classified_records:
            issue("inventory_missing_record_disposition", document_id, record_ref)
    for record_ref in emitted - known_records:
        issue("inventory_unknown_emitted_record", record_ref=record_ref)
    for record_ref in emitted - relevant_records:
        if record_ref in known_records:
            issue("inventory_unclassified_emitted_record", record_ref=record_ref)
    for identifier in reply_check["unsupported_records"]:
        issue("inventory_unsupported_output_identifier", record_ref=identifier)
    return {
        "passed": not findings,
        "record_coverage_complete": not findings,
        "semantic_completeness": "unverified",
        "expected_documents": len(expected),
        "classified_documents": len(seen_docs),
        "recognized_records": len(source_records),
        "relevant_records": len(relevant_records),
        "emitted_records": len((relevant_records & emitted) - missing_rendered_refs),
        "findings": findings,
    }


def reconcile_inventory_reply(markdown: str, evidence_payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile streaming Markdown without inventing model dispositions."""
    records = inventory_records(evidence_payload)
    coverage = evidence_payload.get("coverage") or {}
    manifest = coverage.get("documents") if isinstance(coverage, dict) else []
    document_ids = {
        row["document_id"] for row in (manifest or [])
        if isinstance(row, dict) and isinstance(row.get("document_id"), str)
    }
    document_ids.update(
        row["document_id"] for row in evidence_payload.get("evidence", [])
        if isinstance(row, dict) and isinstance(row.get("document_id"), str)
    )
    recognized_docs = {document_id for row in records for document_id in row["supporting_document_ids"]}
    known_identifiers = {_record_key(row["identifier"]) for row in records}
    emitted: set[str] = set()
    unsupported: set[str] = set()
    missing_fields: list[dict[str, str]] = []
    uncertain_fields: list[dict[str, str]] = []
    for block, row_fields in _field_rows(markdown):
        text = _MARKER_RE.sub("", block)
        identifiers = {
            re.sub(r"\s+", "", match.group(1) or match.group(0)).upper().replace("．", ".")
            for match in _RECORD_IDENTIFIER_RE.finditer(text)
        }
        citations = set(inline_citation_ids(block))
        identifier_keys = {_record_key(item) for item in identifiers}
        for record in records:
            if _record_key(record["identifier"]) not in identifier_keys:
                continue
            if citations.intersection(record["evidence_ids"]):
                emitted.add(record["record_ref"])
                for field_name, values in record.get("field_values", {}).items():
                    if len(values) != 1:
                        uncertain_fields.append({"record_ref": record["record_ref"], "field": field_name})
                    elif (
                        not _record_field_matches(field_name, values[0], row_fields)
                        or not citations.intersection(record["field_evidence"][field_name])
                    ):
                        missing_fields.append({"record_ref": record["record_ref"], "field": field_name, "value": values[0]})
            else:
                unsupported.add(record["identifier"])
        unsupported.update(item for item in identifiers if _record_key(item) not in known_identifiers)
    missing = [
        {"record_ref": row["record_ref"], "document_id": row["document_id"], "identifier": row["identifier"]}
        for row in records if row["record_ref"] not in emitted
    ]
    uncertain_docs = sorted(document_ids - recognized_docs)
    unreported = max(0, int(coverage.get("expected") or 0) - len(document_ids)) if isinstance(coverage, dict) else 0
    return {
        "complete": bool(isinstance(coverage, dict) and coverage.get("complete") is True)
        and not missing and not unsupported and not uncertain_docs and not unreported
        and not missing_fields and not uncertain_fields,
        "semantic_completeness": "unverified",
        "recognized_records": len(records),
        "emitted_records": len(emitted),
        "missing_records": missing,
        "unsupported_records": sorted(unsupported),
        "missing_fields": missing_fields,
        "uncertain_fields": uncertain_fields,
        "uncertain_document_ids": uncertain_docs,
        "uncertain_document_count": len(uncertain_docs) + unreported,
    }


def detect_prompt_injection(text: str) -> list[str]:
    hits = []
    for match in _INJECTION_RE.finditer(text or ""):
        hits.append(match.group(0)[:80])
    return hits[:20]


def quarantine_excerpt(text: str, *, max_chars: int = 1200) -> str:
    cleaned = (text or "").replace("\x00", " ").strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    # Neutralize common role-play markers without destroying readable content.
    cleaned = cleaned.replace("```", "'''")
    return cleaned
