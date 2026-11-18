# -*- coding: utf-8 -*-
"""智造方寸 产品说明书（对外版）。版式按中文技术文档惯例：正文宋体、标题黑体、首行缩进。"""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import sys

OUT = sys.argv[1]
BODY_FONT = "宋体"
HEAD_FONT = "黑体"
LATIN_FONT = "Times New Roman"
BLACK = RGBColor(0x1A, 0x1A, 0x1A)
GRAY = RGBColor(0x55, 0x55, 0x55)
HDR_FILL = "D9D9D9"


def set_font(run, size=10.5, bold=False, color=None, east=BODY_FONT, latin=LATIN_FONT):
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), east)
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    run.font.name = latin


def shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    tcPr.append(shd)


def repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def no_split(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:cantSplit")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def page_field(paragraph):
    set_font(paragraph.add_run("— "), 9, False, GRAY)
    run = paragraph.add_run()
    set_font(run, 9, False, GRAY)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)
    set_font(paragraph.add_run(" —"), 9, False, GRAY)


def h1(doc, text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(16)
    pf.space_after = Pt(8)
    pf.line_spacing = 1.35
    set_font(p.add_run(text), 15, True, BLACK, east=HEAD_FONT, latin=HEAD_FONT)
    return p


def h2(doc, text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(10)
    pf.space_after = Pt(4)
    pf.line_spacing = 1.35
    set_font(p.add_run(text), 12, True, BLACK, east=HEAD_FONT, latin=HEAD_FONT)
    return p


def para(doc, text, size=10.5, bold=False, indent=True, after=6, keep_next=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(after)
    pf.line_spacing = 1.5
    if indent:
        pf.first_line_indent = Pt(size * 2)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    pf.keep_with_next = keep_next
    set_font(p.add_run(text), size, bold, BLACK)
    return p


def item(doc, text, size=10.5):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent = Pt(size * 2)
    pf.first_line_indent = Pt(-size)
    pf.space_after = Pt(3)
    pf.line_spacing = 1.5
    set_font(p.add_run("· " + text), size, False, BLACK)
    return p


def table(doc, headers, rows, widths, font_size=9, header_size=9):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    hdr = t.rows[0]
    repeat_header(hdr)
    no_split(hdr)
    for i, text in enumerate(headers):
        cell = hdr.cells[i]
        cell.width = Cm(widths[i])
        cell.text = ""
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.space_before = Pt(2)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(p.add_run(text), header_size, True, BLACK)
        shade(cell, HDR_FILL)
    for row in rows:
        cells = t.add_row()
        no_split(cells)
        for i, text in enumerate(row):
            cell = cells.cells[i]
            cell.width = Cm(widths[i])
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.line_spacing = 1.2
            set_font(p.add_run(str(text)), font_size, False, BLACK)
    for i, w in enumerate(widths):
        for cell in t.columns[i].cells:
            cell.width = Cm(w)
    return t


def caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    set_font(p.add_run(text), 9, False, GRAY)


doc = Document()

section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.0)
section.right_margin = Cm(3.0)

normal = doc.styles["Normal"]
normal.font.size = Pt(10.5)
normal_rPr = normal.element.get_or_add_rPr()
normal_rFonts = normal_rPr.find(qn("w:rFonts"))
if normal_rFonts is None:
    normal_rFonts = OxmlElement("w:rFonts")
    normal_rPr.append(normal_rFonts)
normal_rFonts.set(qn("w:eastAsia"), BODY_FONT)
normal_rFonts.set(qn("w:ascii"), LATIN_FONT)
normal_rFonts.set(qn("w:hAnsi"), LATIN_FONT)
normal.paragraph_format.line_spacing = 1.5
normal.paragraph_format.space_after = Pt(6)

footer_p = section.footer.paragraphs[0]
footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
page_field(footer_p)

# ---------------- 封面 ----------------
for _ in range(5):
    para(doc, "", indent=False, after=0)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(10)
set_font(p.add_run("智造方寸"), 34, True, BLACK, east=HEAD_FONT, latin=HEAD_FONT)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(4)
set_font(p.add_run("制造企业一体机"), 15, False, BLACK, east=HEAD_FONT, latin=HEAD_FONT)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(60)
set_font(p.add_run("产品说明书"), 15, False, BLACK, east=HEAD_FONT, latin=HEAD_FONT)

table(doc,
      ["项目", "内容"],
      [
          ["产品名称", "智造方寸"],
          ["产品形态", "企业一体机（独立网关，单机部署）"],
          ["功能范围", "研发（图纸、BOM、版本与变更）、采购、销售、库存、生产、质量、仓储，以及企业大模型管理与业务助手"],
          ["文档版本", "V1.0"],
          ["编制日期", "2026 年 10 月"],
          ["密级", "商密"],
      ],
      [3.6, 10.0], 9.5)
doc.add_page_break()

# ---------------- 目录 ----------------
TOC_ITEMS = [
    "一、产品概述",
    "二、适用对象",
    "三、系统构成",
    "四、业务功能",
    "五、企业大模型管理",
    "六、业务助手",
    "七、典型流程",
    "八、应用场景",
    "九、部署与运行环境",
    "十、权限、审计与数据边界",
    "十一、实施与交付",
    "十二、常见问题",
]
TOC_PAGES = {}
if len(sys.argv) > 2:
    import json
    with open(sys.argv[2], "r", encoding="utf-8") as fh:
        TOC_PAGES = json.load(fh)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(6)
p.paragraph_format.space_after = Pt(18)
set_font(p.add_run("目　　录"), 15, True, BLACK, east=HEAD_FONT, latin=HEAD_FONT)
for title in TOC_ITEMS:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(6)
    pf.line_spacing = 1.5
    pf.tab_stops.add_tab_stop(Cm(15.0), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
    set_font(p.add_run(title), 10.5, False, BLACK)
    set_font(p.add_run("\t" + str(TOC_PAGES.get(title, "0"))), 10.5, False, BLACK)
doc.add_page_break()

# ---------------- 一、概述 ----------------
h1(doc, "一、产品概述")
para(doc, "智造方寸是面向中小制造企业的一体化业务系统。它把研发、采购、销售、库存、生产、质量、仓储七类业务放在同一套数据库、同一套权限和同一条单据链上运行，并随系统提供一个可以查询业务数据、检索企业资料、起草文档的人工智能助手。")
para(doc, "系统的起点是轻量 MES。生产订单、派工、领料、工序汇报与检验在早期版本中已经跑通，随后向前延伸到采购到货与入库，向后延伸到销货、拣货与发货，逐步覆盖企业日常运转的主要环节。这套演进路径决定了它和「先买 ERP、再买 MES、最后用 Excel 补缺口」的做法不同：所有模块共用一张存货档案、一份往来单位、一套审批规则。")
para(doc, "智造方寸以独立网关形态交付。一台网关供一个工厂或一个车间使用，整机私有化部署在用户自己的网络环境中，不依赖厂商的云服务。系统的业务功能全部在厂内网运行；人工智能部分通过模型接口调用，接口可以指向厂内部署的模型服务，也可以指向经白名单放行的云端服务。")

h1(doc, "二、适用对象")
para(doc, "本产品面向单厂或少量车间、单仓或多仓的中小型制造企业，典型情况包括：已经有 ERP 但生产与仓储仍在用表格管理的企业；只有零散系统、希望一次把采购到发货拉通的企业；研发图纸与 BOM 版本靠共享盘和文件名管理、需要严格管控修订的企业；以及需要把一套系统整体部署到多个独立站点、各站点自行管理数据的企业。")
para(doc, "以下情况不属于本产品的适用范围：")
item(doc, "需要完整总账、凭证、纳税申报等法定会计功能的企业 —— 本产品不含财务模块，相关需求应继续使用专业财务软件。")
item(doc, "以自动化产线控制、设备联网采集为主要诉求的工厂 —— 本产品定位于业务管理，不对接产线 PLC 与设备网关。")
item(doc, "以多组织、多法人、跨地域合并报表为核心诉求的集团型企业。")

h1(doc, "三、系统构成")
para(doc, "整机由业务门户、业务服务、业务数据库、业务助手、模型接口与资料库六部分组成，全部运行在同一台网关上。")
caption(doc, "表 1  系统组成")
table(doc,
      ["组成部分", "说明"],
      [
          ["业务门户", "浏览器访问的操作界面，列表、表单、明细、参照、选单、导入共用一套面板运行时"],
          ["业务服务", "面板配置、业务规则、审批、生单与库存聚合由服务端统一处理"],
          ["业务数据库", "存放单据、审批记录、库存状态与面板配置"],
          ["业务助手", "对话界面与文档解析、检索、工具调用、文件生成能力"],
          ["模型接口", "读取厂内或云端的大模型服务，用于理解与生成"],
          ["资料库", "存放研发图纸、工艺文件、检验规范及其版本，以及系统生成的交付物"],
      ],
      [3.2, 12.6], 9)
para(doc, "门户与接口同域部署，浏览器访问时不存在跨域问题，登录状态可以正常保持。整机可离线安装，安装完成后按向导设置管理员密码、连接模型服务即可进入使用。")

h1(doc, "四、业务功能")
h2(doc, "4.1 研发管理")
para(doc, "研发管理负责把产品从设计到定型的过程和数据管起来。立项、设计、试制、验证、定型各阶段产生的图纸、物料、BOM、工艺文件与验证记录，都在系统内按项目和版本留存，不再散落在共享盘与个人电脑里。系统把产品身份与修订版分开：物料编码、名称、规格属于身份；图纸、BOM、工艺属于某个具体修订版，下游单据引用修订版，而不是只写一个名称。")
para(doc, "研发物料与设计 BOM。物料档案维护编码规则、规格型号、单位、分类与默认供应商，研发、采购、生产、库存共用同一份档案，避免研发自建一套料号、生产和采购再各建一套。BOM 以父子结构维护多级物料清单，每行记录用量、单位、损耗率、生效区间与替代料；同一产品的不同修订版各持自己的设计 BOM，互不覆盖。")
para(doc, "图纸与文档版本。设计图纸、工艺文件、检验规范按文档对象管理，支持检入检出、版本递增、校验值与权限控制。文件存放在资料库，数据库只记录版本号、校验值、存储引用与授权关系。已发布的修订版不能原地覆盖，修改一律产生新版本并保留审计链，谁在什么时间改了什么都能查到。")
para(doc, "设计变更。研发变更从变更申请开始：填写变更原因、受影响的物料与 BOM、在制与库存的受影响范围，完成影响评估后转为变更执行单，指定执行人与验证人。执行完成后新修订版生效，历史版本与当时的 BOM 结构仍可追溯；已冻结的基线保持不变，便于回溯某批产品依据的是哪一版设计。")
para(doc, "试制与定型。试制阶段的 BOM 与正式 BOM 分开管理，试制数量、用料与验证结果单独记录，避免试制数据混入量产口径。产品定型发布后生成正式修订版，生产订单按已发布的 BOM 取料，超定额领用需要说明原因；检验方案绑定对应的产品与工序版本。一批产品从下达到入库，用到的图纸、BOM、工艺与检验依据可以追溯到同一组版本。")
h2(doc, "4.2 采购管理")
para(doc, "采购模块覆盖从请购分析到入库的完整过程。请购分析按历史消耗与安全库存给出补货建议；到货单登记供应商送货，未完成来料检验的批次不能生成采购入库；采购入库完成后，库存数量与库存台账同步更新。采购交期与到货情况可在报表中按供应商、物料、时间段查询。")
h2(doc, "4.3 销售管理")
para(doc, "销售订单登记客户需求，订单执行情况按产品与交期跟踪。销货单引用销售订单生成，出库前校验库存可用量；发货完成后可查询某一客户、某一产品在指定期间的出货记录。")
h2(doc, "4.4 库存管理")
para(doc, "库存数量以现存量实时聚合为唯一口径，不保存静态快照。系统提供收发存汇总、库存台账、其他入库与其他出库，支持按仓库、存货分类、批次查看。呆滞料与库龄可以按区间筛选，盘点差异单独记录。")
h2(doc, "4.5 生产管理")
para(doc, "生产订单登记产品、数量与交期，自动带出物料需求明细。车间按生产订单派工，工序汇报登记完工数量与工时，领料单按定额发料，超领时提示。产品完工后办理完工入库。委外加工单独管理，包括委外订单、委外发料、委外入库，以及回货进度与欠料查询。")
h2(doc, "4.6 质量管理")
para(doc, "质量控制贯穿来料、首件、过程与完工四个环节。来料检验不合格时拦截入库；首件检验未通过不得批量生产；过程检验按检验方案抽检，结果决定工序放行或返工；完工检验完成后才可以办理完工入库。检验项目、检验方案、不良品处理与返工记录集中管理，质量统计分析按产品、工序、供应商汇总。")
h2(doc, "4.7 仓储与序列号")
para(doc, "仓储作业支持其他出入库、调拨、货位调整与盘点，拣货单用于按销货需求集中拣货。移动仓管页面面向手持设备，覆盖入库、上下架、出库与货位调整四个动作。序列号管理包括启用、入库登记、出库扫描，以及状况表与跟踪表查询，可沿序列号正向追溯到客户、反向追溯到原材料批次。")

h1(doc, "五、企业大模型管理")
para(doc, "不少企业对大模型的使用是散的：各部门自己申请账号和密钥，各自挑模型，费用摊不清；谁把什么内容发给了哪个模型，事后也说不清楚。系统把模型调用收拢到一处管理，业务人员不接触密钥，只负责使用。")
para(doc, "统一登记。所有模型统一登记为模型端点，登记项包括接口地址、协议（OpenAI 兼容、Azure、Anthropic）、密钥、可用模型清单以及能力标记（对话、向量、重排、图像理解）。密钥由系统托管，界面上只显示末尾几位，换人不换密钥。")
para(doc, "统一路由。对话、写作、向量化、重排四类用途分别指定默认端点，也可以按部门或场景单独指定；端点故障时按策略回退。回退只允许指向同等或更低数据密级的端点，不会因为主端点不可用就把机密内容发到公开模型上。")
caption(doc, "表 2  模型接入方式")
table(doc,
      ["接入方式", "做法", "适用场景"],
      [
          ["厂内模型服务", "在厂内网部署兼容 OpenAI 接口的服务（vLLM、Ollama 等），系统指向内网地址", "高密级资料、要求全内网、有算力投入"],
          ["云端模型接口", "放行指定域名的出站访问，密钥由系统托管", "不自建算力、按量付费、资料密级可控"],
          ["混合", "文档检索与向量化在本地完成，生成环节调用云端接口", "多数企业的实际选择"],
      ],
      [3.2, 7.6, 5.4], 9)
para(doc, "数据分级。每个端点登记可接收的最高数据密级，分为公开、内部、机密、受限四级。待处理内容的密级高于端点登记级别时，系统拒绝发送并在界面说明原因，不会静默降级为低密级内容；出站流量只允许访问已登记的模型域名，其余默认拒绝。")
para(doc, "用量与预算。系统按用户、部门、端点统计调用次数、令牌用量与费用，可设置月度额度与单次运行成本上限，接近额度预警，超过上限硬停。定时任务单独核算预算，避免夜间批量运行把额度用穿。")
para(doc, "稳定性与可替换。端点可设置并发上限、排队等待与超时重试，支持一键连通性测试。换供应商、从云端切到内网模型，只需要改端点配置，业务功能与提示词不动。内网模型与云端接口使用同一套管理方式。")

h1(doc, "六、业务助手")
para(doc, "业务助手是系统内置的人工智能应用，具备四类能力：查询业务数据，例如某个产品当前在制数量、哪些到货批次尚未检验；检索企业资料，把工艺文件、检验规范、客户标准导入资料库后按问题检索并给出出处；起草文档，按系统数据生成质量报告、日报周报、汇报材料；按时提醒，把待审单据、异常情况推送到企业微信或钉钉。")
para(doc, "助手的定位是辅助而不是替代。它只能读取数据并生成草稿，不能直接修改单据、不能代替审批人签字，所有写入操作仍然由用户在界面上确认。回答涉及业务数据时附带单据号，涉及文档内容时附带原文位置，查不到结果时明确说明查不到，不做推测。")

h1(doc, "七、典型流程")
para(doc, "以采购到发货为主线，系统内的单据按下列顺序流转，每个环节都有对应的校验，不满足条件时无法进入下一步。")
para(doc, "请购分析 → 到货单 → 来料检验 → 采购入库 → 生产订单与领料 → 派工与工序汇报 → 首件、过程、完工检验 → 完工入库 → 拣货与销货 → 发货", indent=False, bold=False)
caption(doc, "表 3  单据链控制点")
table(doc,
      ["环节", "控制要求", "系统处理"],
      [
          ["到货入库", "未检验不得入库", "来料检不合格时拦截，合格后生成采购入库"],
          ["领料生产", "按生产订单定额发料", "超领提示，缺料预警"],
          ["汇报检验", "首件必检，过程检按方案抽检", "检验结果决定工序放行或返工"],
          ["完工入库", "未完工不得入库，来源单须已审核", "生单与选单校验来源状态"],
          ["销货发货", "未审核不得发货，库存不足需提示", "来源单状态校验与可用量控制"],
          ["全部环节", "不可跳步，不可越权", "审批状态与记录同事务推进，权限按动作校验"],
      ],
      [2.8, 6.0, 7.0], 9)
para(doc, "举一个实际例子。某批来料在到货单登记后，检验员在系统中按检验方案录入结果，其中一项尺寸超差。系统据此判定该批次不合格，到货单无法生成采购入库单，界面上提示「来料检验未通过，请先处理不良品」。质量工程师登记不良品并选择退货或让步接收，处理完成后该批次才允许入库。整条过程的检验记录与审批记录都可以在单据上查到。")

h1(doc, "八、应用场景")
para(doc, "质量周报。质量工程师不再手工汇总检验记录。在助手中选择产品与时间段，系统按产品、工序、供应商汇总检验批次、合格率与主要不良项，生成周报草稿，包含本期数据、环比变化与需要关注的三类问题，工程师修改后即可发出。计算结果全部来自检验单据，草稿中每一项数据都可以点回原单据核对。")
para(doc, "库存呆滞与库龄。仓管员按仓库与存货分类查询库龄分布，系统列出超过设定天数未发生出入库的物料、当前数量与金额占比，并给出最近一次出入库时间。对于长期呆滞的物料，可以进一步查询它最近一次被哪些生产订单领用，判断是否可以处理。")
para(doc, "委外欠料跟踪。委外发料后，计划员需要知道哪些委外订单尚未回货、哪些物料发出后迟迟没有回音。系统按委外订单列出已发料、已回货、欠料数量与超期天数，超期订单在列表中优先显示，可导出为跟催清单。")
para(doc, "此外，采购到货与检验状态、生产日报、移动仓管扫码作业、序列号追溯、生产与质量月报等场景均有对应实现，可按企业实际需要分批启用。")

h1(doc, "九、部署与运行环境")
para(doc, "整机以单机方式部署，一台网关对应一个使用单位。硬件可按下列配置选型，实际配置可根据并发用户数与资料规模调整。")
caption(doc, "表 4  建议硬件配置")
table(doc,
      ["项目", "建议配置"],
      [
          ["整机形态", "小型工控机或加固笔记本，可装箱携带，也可固定安装"],
          ["处理器与内存", "8 核以上；内存 32 GB 起"],
          ["存储", "1–2 TB 固态硬盘，用于业务库、资料库与每日备份"],
          ["网络", "双千兆网口接入车间内网；可选无线热点与 4G/5G 上行"],
          ["供电与散热", "220 V 供电或内置电池；优先无风扇机型，适应粉尘与振动环境"],
      ],
      [3.4, 12.4], 9)
para(doc, "业务功能全部在厂内网运行，断网不影响单据、审批、库存与报表。需要使用智能问答与自动成稿时，系统通过已登记的模型端点调用，端点可以指向厂内模型服务，也可以指向按白名单放行的云端接口，具体见第五章。")

h1(doc, "十、权限、审计与数据边界")
para(doc, "系统按动作授权，共十一项：查看、查询、新增、修改、删除、导出、打印、审核、价格、复核、调整。角色与面板之间按动作粒度配置，用户只能看到被授权的菜单与按钮。按钮在无权限时置灰，同时阻止事件与请求，不依赖前端隐藏。")
para(doc, "审批状态与审批记录在同一事务内推进，审批通过或驳回时校验最近一条待审批提交记录；已生成下游单据的来源单不允许直接删除。所有关键操作写入审计日志，包含操作人、时间、对象与结果。")
para(doc, "数据全部保存在网关本地。整机支持备份与换机迁移，迁移时连同业务库与资料库一起复制。使用云端模型时，只有用户在当前会话中实际提交的内容会被发送，系统不主动上传资料库全文。")

h1(doc, "十一、实施与交付")
para(doc, "实施按四个阶段推进，每个阶段有明确的交付物，便于双方确认进度。")
caption(doc, "表 5  实施阶段与交付物")
table(doc,
      ["阶段", "主要工作", "交付物"],
      [
          ["现场准备", "硬件就位、内网与端口确认、账号与权限梳理", "部署清单、网络确认表"],
          ["基础配置", "主数据导入、面板与字段调整、审批与权限配置", "可运行的业务基线"],
          ["上线试运行", "关键单据链跑通、双轨核对、问题清单闭环", "试运行报告、问题清单"],
          ["培训与移交", "分角色培训、操作手册、备份与运维约定", "操作手册、培训记录"],
      ],
      [3.0, 8.4, 5.4], 9)
para(doc, "交付内容包含整机硬件、系统镜像、初始化配置与培训。后续新增单据或调整字段，通过面板配置完成，不修改程序，也不需要重新部署。")

h1(doc, "十二、常见问题")
para(doc, "问：不联网能不能用？", bold=True, indent=False, after=2, keep_next=True)
para(doc, "答：业务功能可以，智能问答与自动成稿需要模型接口。若要求全内网运行，需在厂内自行部署模型服务，系统按内网地址调用。")
para(doc, "问：能不能直接替我们记账、出报表给税务？", bold=True, indent=False, after=2, keep_next=True)
para(doc, "答：不能。本产品不含财务模块，不生成会计凭证，也不做纳税申报，相关业务请继续使用专业财务软件。")
para(doc, "问：数据会不会被上传到厂商服务器？", bold=True, indent=False, after=2, keep_next=True)
para(doc, "答：业务数据与资料保存在用户自己的网关上。使用云端模型接口时，只有当前会话提交的内容会发往接口，接口可登记密级，超限内容一律不发送。")
para(doc, "问：以后想加功能怎么办？", bold=True, indent=False, after=2, keep_next=True)
para(doc, "答：多数调整属于配置范畴，例如新增单据、增减字段、调整审批环节，通过面板配置完成。涉及新业务逻辑的，需要单独评估与开发。")
para(doc, "问：多台网关之间数据能不能汇总？", bold=True, indent=False, after=2, keep_next=True)
para(doc, "答：默认各站点数据独立。如需汇总，可单独规划数据上报链路，本产品当前不默认开启跨站点数据同步。")

doc.save(OUT)
print("saved", OUT)
