---
name: pptx
description: 使用 python-pptx 原生 API 读取、生成和编辑 PowerPoint，并用离线工具渲染验收
category: document-processing
license: MIT. See LICENSE
---

# PPTX 读取、生成、编辑与预览

本技能直接使用 MIT 许可的 `python-pptx` 与 Pillow，不维护另一套 PPTX 生成 DSL。`scripts/pptx_tool.py` 只提供两项原生库不负责的通用能力：结构检查（`inspect`）与 LibreOffice/Poppler 视觉渲染（`render`）。

## 平台约束

- 当前技能目录只读。把“当前 `SKILL.md` 所在目录”的绝对路径导出为 `PPTX_SKILL_DIR`；工具入口是 `$PPTX_SKILL_DIR/scripts/pptx_tool.py`。
- 输入、工作脚本和最终文件放在 `/workspace`；验收图片等中间结果放在 `/workspace/.pi/`，避免被平台收集为交付成果。临时转换文件放在 `/tmp`。不要改写技能目录。
- 镜像已固定提供 Python 3、`python-pptx==0.6.23`、Pillow、lxml、LibreOffice（`soffice`）、Poppler（`pdftoppm`）及中英文字体。不要安装或下载依赖。
- 生成或编辑必须写到新文件，除非用户明确要求覆盖原件。
- 修改现有文稿前先 `inspect`；完成后再次 `inspect` 并 `render`，真实查看联系表和可疑单页。

```bash
export PPTX_SKILL_DIR="<当前 SKILL.md 所在绝对目录>"
PPTX_TOOL="$PPTX_SKILL_DIR/scripts/pptx_tool.py"
```

## 读取与定位

先输出结构 JSON。页码和 shape 索引均从 `0` 开始：

```bash
python3 "$PPTX_TOOL" inspect /workspace/input.pptx \
  --output /workspace/input-structure.json
```

结构包含页面尺寸、元数据、备注、shape 名称/类型/位置、文本段落、表格和图表系列。完整读取 JSON 后，再在工作脚本中使用同一索引。需要访问更深的原生对象时：

```python
from pptx import Presentation

prs = Presentation("/workspace/input.pptx")
print(prs.slide_width, prs.slide_height, len(prs.slides))
for slide_index, slide in enumerate(prs.slides):
    print("slide", slide_index)
    for shape_index, shape in enumerate(slide.shapes):
        print(shape_index, shape.name, shape.shape_type)
        if getattr(shape, "has_text_frame", False):
            print(shape.text)
        if getattr(shape, "has_table", False):
            print([[cell.text for cell in row.cells] for row in shape.table.rows])
    if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
        print("notes:", slide.notes_slide.notes_text_frame.text)
```

## 生成文稿

在 `/workspace` 编写一次性 Python 脚本，直接调用 `python-pptx`。以下范式可运行，并展示页面、文本、形状、表格、图表、备注和元数据；按任务删减，不要复制无用元素：

```python
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

OUT = "/workspace/output.pptx"
prs = Presentation()
prs.slide_width = Inches(13.333333)
prs.slide_height = Inches(7.5)
prs.core_properties.title = "季度经营复盘"
prs.core_properties.author = "Doc Nexus"
blank = prs.slide_layouts[6]

slide = prs.slides.add_slide(blank)
slide.background.fill.solid()
slide.background.fill.fore_color.rgb = RGBColor.from_string("F7F4ED")

def add_text(slide, text, x, y, w, h, size, color="172B4D", bold=False,
             align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0)
    paragraph = frame.paragraphs[0]
    paragraph.text = text
    paragraph.alignment = align
    paragraph.font.name = "Noto Sans CJK SC"
    paragraph.font.size = Pt(size)
    paragraph.font.bold = bold
    paragraph.font.color.rgb = RGBColor.from_string(color)
    return box

add_text(slide, "季度经营复盘", 0.7, 0.45, 11.9, 0.65, 30, bold=True)
add_text(slide, "关键结论与下一步行动", 0.72, 1.18, 11.8, 0.4, 16, "52606D")

accent = slide.shapes.add_shape(
    MSO_SHAPE.ROUNDED_RECTANGLE,
    Inches(0.75), Inches(2.0), Inches(4.7), Inches(2.4),
)
accent.fill.solid()
accent.fill.fore_color.rgb = RGBColor.from_string("DCE8F5")
accent.line.color.rgb = RGBColor.from_string("8AA7C2")
add_text(slide, "收入同比增长 18%\n交付周期缩短 12 天", 1.05, 2.35, 4.1, 1.5, 20)

chart_data = ChartData()
chart_data.categories = ["Q1", "Q2", "Q3", "Q4"]
chart_data.add_series("收入", [120, 148, 169, 201])
chart = slide.shapes.add_chart(
    XL_CHART_TYPE.COLUMN_CLUSTERED,
    Inches(5.9), Inches(1.9), Inches(6.5), Inches(4.6), chart_data,
).chart
chart.has_title = True
chart.chart_title.text_frame.text = "季度收入（万元）"
chart.has_legend = False
chart.category_axis.has_title = True
chart.category_axis.axis_title.text_frame.text = "季度"
chart.value_axis.has_title = True
chart.value_axis.axis_title.text_frame.text = "万元"

notes = slide.notes_slide.notes_text_frame
if notes is not None:
    notes.text = "先讲增长，再说明交付效率改善。"

summary = prs.slides.add_slide(blank)
add_text(summary, "行动计划", 0.7, 0.45, 11.9, 0.65, 30, bold=True)
table_shape = summary.shapes.add_table(
    3, 3, Inches(0.8), Inches(1.6), Inches(11.7), Inches(3.8)
)
table = table_shape.table
for row, values in enumerate([
    ["事项", "负责人", "截止时间"],
    ["重点客户复盘", "销售团队", "9 月 30 日"],
    ["交付流程优化", "交付团队", "10 月 15 日"],
]):
    for column, value in enumerate(values):
        table.cell(row, column).text = value
        for paragraph in table.cell(row, column).text_frame.paragraphs:
            paragraph.font.name = "Noto Sans CJK SC"
            paragraph.font.size = Pt(14)
            paragraph.font.bold = row == 0

prs.save(OUT)
print(OUT)
```

### 图片与版式

使用 `slide.shapes.add_picture(path, left, top, width=..., height=...)` 添加图片。先用 Pillow 读取像素宽高并计算等比例尺寸，不要拉伸。需要裁切时使用返回的 Picture 对象的 `crop_left`、`crop_right`、`crop_top`、`crop_bottom`。

所有坐标使用 `Inches`，字号与线宽使用 `Pt`，颜色使用 `RGBColor.from_string("RRGGBB")`。宽屏页面为 13.333×7.5 英寸。正文通常不小于 16pt；图表要有标题或轴标签；每页聚焦一个结论并保留足够边距。

## 编辑现有文稿

必须在检查过的 shape 上操作。优先修改公开 API 支持的文本、表格、图片、图表数据、备注和页面内容，不要重建整份文稿。以下范式读取原件、修改明确对象、追加元素、调整页序并另存：

```python
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

SOURCE = "/workspace/input.pptx"
OUTPUT = "/workspace/edited.pptx"
prs = Presentation(SOURCE)

# inspect 已确认：第 0 页 shape 1 是标题文本框。
title = prs.slides[0].shapes[1]
frame = title.text_frame
frame.clear()
paragraph = frame.paragraphs[0]
paragraph.text = "更新后的标题"
paragraph.font.name = "Noto Sans CJK SC"
paragraph.font.size = Pt(30)
paragraph.font.bold = True
paragraph.font.color.rgb = RGBColor.from_string("172B4D")

# inspect 已确认：第 1 页 shape 3 是表格。
table = prs.slides[1].shapes[3].table
table.cell(1, 2).text = "已完成"

# 在既有页追加说明。
box = prs.slides[1].shapes.add_textbox(
    Inches(0.8), Inches(6.6), Inches(11.7), Inches(0.3)
)
box.text_frame.paragraphs[0].text = "数据截至 2026-09-19"
box.text_frame.paragraphs[0].font.size = Pt(10)

# 修改备注。
notes = prs.slides[1].notes_slide.notes_text_frame
if notes is not None:
    notes.text = "此页先讲结果，再讲风险。"

# 删除页面：从后往前删，避免索引漂移。
def delete_slide(presentation, index):
    slide_id = presentation.slides._sldIdLst[index]
    presentation.part.drop_rel(slide_id.rId)
    del presentation.slides._sldIdLst[index]

# delete_slide(prs, 4)

# 重排页面：order 必须是当前全部索引的完整排列。
def reorder_slides(presentation, order):
    if sorted(order) != list(range(len(presentation.slides))):
        raise ValueError("order 必须是全部页面索引的完整排列")
    nodes = list(presentation.slides._sldIdLst)
    for node in nodes:
        presentation.slides._sldIdLst.remove(node)
    for index in order:
        presentation.slides._sldIdLst.append(nodes[index])

# reorder_slides(prs, [1, 0, 2, 3])
prs.save(OUTPUT)
print(OUTPUT)
```

`python-pptx` 对动画、宏、数字签名、嵌入对象和部分高级 OOXML 没有完整公开 API。遇到这些内容时只修改已确认的公开对象，并通过新文件保留原件；不要声称未验证的高级特性已保留。`.pptm`、密码保护文件或必须保持签名的文件不应另存。

## 渲染与真实验收

`render` 使用唯一的 `/tmp` LibreOffice profile，不写只读根文件系统。输出目录必须不存在或为空：

```bash
python3 "$PPTX_TOOL" render /workspace/output.pptx \
  --output-dir /workspace/.pi/output-preview --dpi 120 --columns 4
```

工具输出逐页 `slide-XXX.png` 和 `contact-sheet.png`。必须打开联系表，并对可疑页面查看单页大图；重点检查文本截断、重叠、乱码、越界、低对比度、图片变形和不可读图表。修复后重新保存并渲染全部页面。

## 交付检查

1. 最终文件可由 `inspect` 成功读取，页数、文本、表格、图表系列和备注符合预期。
2. `render` 成功，联系表及可疑单页已经真实查看。
3. 最终 `.pptx` 位于 `/workspace`，未覆盖用户原件（除非用户明确要求）。
4. 只交付最终文稿和用户要求的预览；一次性工作脚本与中间文件不作为成果。
