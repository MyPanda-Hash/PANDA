from __future__ import annotations

import re
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from app.services.markdown_docx import (
    TABLE_TEXT_WIDTH_TWIPS,
    markdown_to_docx_bytes,
    markdown_to_paragraphs,
    render_project_docx_body,
)


def test_markdown_headings_lists_and_inline_styles_become_ooxml():
    paragraphs = markdown_to_paragraphs(
        "\n".join(
            [
                "# 主标题",
                "",
                "## 项目概况",
                "",
                "这是**加粗**与*斜体*，还有`代码`。",
                "",
                "- 要点一",
                "- 要点二",
                "",
                "1. 第一步",
                "2. 第二步",
                "",
                "> 引用说明",
                "",
                "详见 [官网](https://example.com)。",
            ]
        )
    )
    joined = "".join(paragraphs)
    assert 'w:pStyle w:val="Heading1"' in joined
    assert 'w:pStyle w:val="Heading2"' in joined
    assert "主标题" in joined
    assert "项目概况" in joined
    assert "<w:b/>" in joined
    assert "<w:i/>" in joined
    assert "Consolas" in joined
    assert "• 要点一" in joined
    assert "1. 第一步" in joined
    assert "引用说明" in joined
    assert "官网" in joined
    assert "https://example.com" in joined
    assert "#" not in joined.replace("Heading", "")
    assert "**加粗**" not in joined
    assert "*斜体*" not in joined


def test_markdown_table_becomes_ooxml_table():
    paragraphs = markdown_to_paragraphs(
        "\n".join(
            [
                "## 阶段验收",
                "",
                "| 阶段 | 验收方式 | 验收标准 |",
                "| --- | :---: | ---: |",
                "| 筹备启动 | 启动会评审 | 方案、预算就绪 |",
                "| 周度数据评审 | 数据达标 | 完成率≥95% |",
            ]
        )
    )
    joined = "".join(paragraphs)
    assert "<w:tbl>" in joined
    assert joined.count("<w:tr>") == 3
    assert paragraphs[-1].startswith("<w:tbl>")
    assert "<w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>" in joined
    assert "阶段" in joined
    assert "验收方式" in joined
    assert "| 阶段 |" not in joined
    assert "---" not in joined
    # LibreOffice collapses a table without a grid, so the grid is explicit and
    # sized from the cells: the widest column (验收标准) must get the most room.
    column_widths = [
        int(width) for width in re.findall(r'<w:gridCol w:w="(\d+)"/>', joined)
    ]
    assert len(column_widths) == 3
    assert sum(column_widths) == TABLE_TEXT_WIDTH_TWIPS
    assert column_widths[2] > column_widths[0]
    assert re.findall(r'<w:tcW w:w="(\d+)" w:type="dxa"/>', joined)[:3] == [
        str(width) for width in column_widths
    ]


def test_docx_table_has_readable_cell_and_block_spacing():
    document = markdown_to_docx_bytes(
        "## 验收概览\n\n引言段落。\n\n"
        "| 阶段 | 结果 |\n| --- | --- |\n| 启动 | 已完成 |\n\n"
        "表格之后的总结。"
    )
    with ZipFile(BytesIO(document)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))

    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def tag(name: str) -> str:
        return f"{{{namespace}}}{name}"

    body = root.find(f".//{tag('body')}")
    assert body is not None
    blocks = list(body)
    table_index = next(
        index for index, block in enumerate(blocks) if block.tag == tag("tbl")
    )
    for offset in (-1, 1):
        gap = blocks[table_index + offset]
        assert gap.tag == tag("p")
        spacing = gap.find(f"{tag('pPr')}/{tag('spacing')}")
        assert spacing is not None
        assert int(spacing.get(tag("line"), "0")) >= 120
        assert spacing.get(tag("lineRule")) == "exact"

    table = blocks[table_index]
    for cell in table.findall(f".//{tag('tc')}"):
        margins = cell.find(f"{tag('tcPr')}/{tag('tcMar')}")
        assert margins is not None
        for side in ("top", "bottom", "left", "right"):
            margin = margins.find(f"{tag(side)}")
            assert margin is not None
            assert int(margin.get(tag("w"), "0")) >= 100


def test_evidence_markers_are_removed_from_docx():
    joined = "".join(
        markdown_to_paragraphs(
            "5. 方案取舍\n\n"
            "慧云科技专注AIOps垂直领域"
            "^[writing-evidence:ver_4-ifldBWFV13Xg:1801-2694]，"
            "技术岗位门槛高。"
            "[citation:writing-evidence:ver_Fqhgc4qf8aipVg:14-900]"
            "后续句。[citation:0]结尾。"
        )
    )
    assert "writing-evidence:" not in joined
    assert "citation:" not in joined
    assert "垂直领域，技术岗位门槛高。后续句。结尾。" in joined


def test_markdown_highlight_becomes_ooxml_highlight():
    paragraphs = markdown_to_paragraphs("前言==重点结论==后续")
    joined = "".join(paragraphs)
    assert '<w:highlight w:val="yellow"/>' in joined
    assert "重点结论" in joined
    assert "前言" in joined
    assert "后续" in joined
    assert "==重点结论==" not in joined


def test_exported_body_carries_no_citation_markers_or_source_list():
    """交付文件不展示引用：机器 ID、^[…] 约定与 [标签#序号] 都不得写入正文。"""
    body = render_project_docx_body(
        "交付稿",
        [(
            "正文",
            "响应时间≤500ms ^[报价文档]，另有 [检测报告#6] 与 "
            "^[writing-evidence:ver_a:0-5]。",
        )],
    )
    assert "响应时间≤500ms" in body
    assert "报价文档" not in body
    assert "#6" not in body
    assert "writing-evidence" not in body
    assert "证据来源" not in body

    linked = render_project_docx_body(
        "交付稿", [("正文", "见 [官网](https://example.com) 与 [备注]。")]
    )
    # 普通 Markdown 链接与正文方括号不是引用，必须原样保留。
    assert "官网" in linked
    assert "https://example.com" in linked
    assert "备注" in linked


def test_exported_body_preserves_literal_markers_inside_code():
    """代码块与行内代码里的字面标记是示例内容，交付时不得被清洗。"""
    citation_id = f"writing-evidence:ver_a:{'0' * 64}:0-5"
    body = render_project_docx_body(
        "交付稿",
        [(
            "正文",
            "示例：\n\n```text\n写入 ^[报价文档] 表示引用\n"
            f"机器绑定 ^[{citation_id}]\n```\n\n"
            f"行内 `^[检测报告#6]` 与 `^[{citation_id}]` 也是示例。\n\n"
            f"真实事实 500ms ^[报价文档]。",
        )],
    )
    assert "^[报价文档] 表示引用" in body
    assert "^[检测报告#6]" in body
    # 机器 citation ID 在代码里是字面量，在正文里必须剥掉。
    assert body.count(citation_id) == 2
    assert "真实事实 500ms。" in body

    # 表格行内删除标记后不留双空格。
    table = render_project_docx_body(
        "交付稿", [("正文", "| 指标 | 数值 |\n| --- | --- |\n| 准确率 | 95% ^[检测报告#6] |")]
    )
    assert "95% ^[检测报告#6]" not in table
    assert "95%  " not in table


def test_markdown_table_header_never_keeps_citation_markers():
    """表头走 verbatim 的 run 输出，必须自己清洗，否则直接入口会泄漏标记。"""
    joined = "".join(
        markdown_to_paragraphs(
            "| 指标 ^[报价文档] | 数值 |\n| --- | --- |\n| 500ms | 95% |"
        )
    )
    assert "指标" in joined
    assert "报价文档" not in joined


def test_render_project_strips_duplicate_leading_title_heading():
    body = render_project_docx_body(
        "落地案例：奥北科技园展厅数字员工",
        [
            (
                "落地案例：奥北科技园展厅数字员工",
                "# 落地案例：奥北科技园展厅数字员工\n\n## 项目概况\n\n正文段落。",
            )
        ],
    )
    assert body.count("落地案例：奥北科技园展厅数字员工") == 1
    assert 'w:pStyle w:val="Heading2"' in body
    assert "正文段落。" in body
    assert "# 落地案例" not in body
    assert "## 项目概况" not in body
