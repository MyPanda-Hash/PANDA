# 来料/成品检验单（INSPECTION / T+ #QM15）面板：真实抓取对照分析 + 完整配置设计

| 属性 | 内容 |
|---|---|
| 文档类型 | 场景设计 |
| 适用场景 | 质量与车间 |
| 维护状态 | 调研基线 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../../docs/README.md) |

> 对照对象：真实 T+ 机械行业账套 h4t.chanjet.com，单据页 `QM15 来料/成品检验单`（2026-08-19 抓取）
> - 抓取产物：`docs/ref/tplus-live/mech-20260819/qc/inspection-list.dom.json`（cols 列定义 / query 查询字段 / toolbar+topText 工具栏下拉全名 / tabs / strips；1383 行，URL `BAPView/Voucher.aspx?sysId=QM&mId=QM15`，rowCount 505）
> - 配置模板：`frontend/src/business/engine.js` `PU_ORDER_CONFIG`（L2041 起）+ `tools/panels/PU_IN/PU_IN.config.js` 完整产出范例（同批开发结构基准；`ARRIVAL_IN_CONFIG` 在 engine.js 中暂不存在，故以 PU_IN 结构为准）
> - 规范：`docs/页面开发规范.md`（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名、表尾/固定 5 行/列宽）、`docs/design/面板交互设计规范.md`（§2 表头/§3 选单/§4 工具栏/§8 审批/§9 编号/§12 删除组/§18 导入）
> 本文档为**独立设计产物**，未修改任何共享代码；合并由主会话完成。
> 交付物：INSPECTION.config.js（engine.js 格式，可直接拼入）/ INSPECTION.notes.md

---

## 一、真实面板结构提取（机械行业账套 #QM15 来料/成品检验单）

### 1.1 查询字段（表头字段区，真实 11 项）
`*单据日期 / *单据编号 / *业务类型 / 检验部门 / 检验员 / 检验合格数量自动入库 / 让步接收数量自动入库 / 报废数量自动入库 / 供应商 / 采购订单号 / 加工单号`
→ 本配置 **6 项**（任务 5-6 个）：单据日期、单据编号、业务类型（来料检验/成品检验）、检验类别（任务指定，替代 检验部门）、供应商（参照 PARTNER）、检验员（参照 EMP）。

### 1.2 列表可见列（A 区主明细网格，dom.json cols hidden=false 顺序截取）
| # | 列名（真实） | 真实字段名 | 必填 | 本配置 |
|---|---|---|---|---|
| 1 | *存货名称 | Inventory | * | 保留（参照 INV，refMap 带出 存货编码/规格型号/计量单位） |
| 2 | BOM版本号 | Bom |  | 省略（BOM 选料列，非检验录入核心） |
| 3 | 供应商(库存) | DetailPartner |  | 省略（表头已有 供应商，避免重复） |
| 4 | 仓库 | Warehouse |  | 保留（参照 WH） |
| 5 | 批号(合格) | Batch |  | 保留为 检验批号（文本） |
| 6 | *检验方式 | InspectType | * | 保留（下拉框 全检/抽检） |
| 7 | 抽检比例% | SamplingRatio |  | 保留（小数，默认 100） |
| 8 | *计量单位 | Unit | * | 保留（下拉框 件/kg/套/升） |
| 9 | 报检数量 | ArrivalQuantity |  | 保留（小数，必填） |
| 10 | 应检数量 | CanInspectQuantity |  | 省略字段定义（任务 18 字段上限内未纳入；种子行不携带） |
| 11 | *检验数量 | Quantity | * | 保留（小数，必填） |
| 12 | 检验结果判定 | InspectState |  | 保留（下拉框 合格/不合格/让步接收/报废；真实「检验结果+判定」合并列） |
| 13 | 合格数量 | QualifiedQuantity |  | 保留（小数） |
| 14 | 不合格数量 | UnQualifiedQuantity |  | 保留（小数） |
| 15 | 验收退回数量 | ReturnedQuantity |  | 省略字段定义（见决策点 D4） |
| 16 | 让步接收数量 | ConcessionQuantity |  | 省略字段定义（见决策点 D4） |
| 17 | 报废数量 | ScrapQuantity |  | 省略字段定义（见决策点 D4） |
| 18 | 不合格原因 | MultiUnqualifiedReasonSelect |  | 省略字段定义（多选弹窗列，见决策点 D4） |
| 19 | 让步接收含税单价 | ConcessionTaxPrice |  | 省略（让步计价，非核心录入） |
| 20 | 检验方案 | InspectPlan |  | 省略（方案档案列，未建 检验方案 档案面板） |
| 21 | 记录样本明细 / 样本明细个数 | ReInspectDetail / InspectDetailCount |  | 省略（弹窗统计列） |
| 22+ | 检验项目（弹窗）/ 是否录入检验明细 / 货位 / 保质期系列 / 换算率 / 自定义项 | InspectReport / HasInputProjectDetails / … |  | 省略（附属子表与行业扩展） |
| 23 | 检验项目明细子表 | InspectProject_Code / InspectProject / Content / Standard / Result / InspectDateTime / Singlejudgment |  | 平铺为明细字段：检验项目 / 检验日期 / 判定（见决策点 D2） |

> 第二组可见列（存货名称/计量单位/检验数量/合格数量/不合格数量/报废数量）为「选存货」参照网格；不合格处理页签（不合格原因/处理方式/报废类型/报废的结算费用/仓库）与 检验项目明细子表（编码/项目/内容/标准/结果/时间/单项判定合格）为附属子表，不在主网格复刻（见决策点 D1/D2）。

### 1.3 工具栏（真实分组 + 下拉子项全名，dom.json toolbar/topText）
| 组 | 子项（下拉全名） |
|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 |
| 选单 | 选到货单 / 选成品报检单 / 设置默认功能 |
| 保存 | 保存(Alt+S) / 保存新增(Alt+\) / 保存为草稿 / 保存为常用单据 / 保存打印(Alt+G) / 设置默认功能 |
| 修改 | 修改 |
| 删除 | 删除 |
| 审核 | 审核 / 弃审 / 弃审 / 审批情况 |
| 生单 | 生成采购入库单 / 生成产成品入库单 / 生成委外入库单 / 生成到货退回单 / 生成成品报检退回单（生产报检） / 生成成品报检退回单（委外报检） / 设置默认功能 |
| 变更 | 变更 |
| 生成批号 | 生成明细批号 / 生成不合格品批号 / 设置默认功能 |
| 工具 | 现存量查询 / 变更历史 / 设置默认功能 |
| 联查 | 到货情况 |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置 |
| 打印 | 打印 |
| 更多 | 更多 / 更多 / 更多（topText 截断，按现有面板补齐） |

### 1.4 页签（真实 2 个）
明细（主网格）/ 不合格处理（不合格原因+处理方式+报废类型 子表）→ 本配置取 明细 单页签（任务要求 detail.tabs[0]），不合格处理见决策点 D1。

---

## 二、INSPECTION_CONFIG 设计说明

### 2.1 metadata
- `panelCode: 'INSPECTION'` / `panelName: '来料/成品检验单'` / `panelCategory: '单据'`（表尾 备注+审核行 自动显示）
- `autoCodeField: '单据编号'`（标准单据自动编码，前缀 JY- 登记由主会话做）
- `panelState: { dataName: '单据状态', defaultOptions: ['草稿','已审核','已中止'] }`（任务指定三态；未加「审批中」→ 审核走直接过审，不并入 APPROVAL_PANELS 审批流）
- `panelPageDto.tablePages[0]`：
  - tableName：检验单列表；queryFields 6 项（见 §一.1）
  - gridTabs[0] 明细 = 18 列（存货编码/存货名称/规格型号/检验批号/仓库/检验方式/抽检比例%/计量单位/报检数量/检验数量/合格数量/不合格数量/合格率/检验结果判定/检验员/检验日期/检验项目/判定）
  - gridTabs[1] 汇总（summary:true）= 存货名称/规格型号/计量单位/报检数量/检验数量/合格数量/不合格数量/检验结果判定（按 存货名称 分组）
  - topBarBtn / rowOperationBarBtn / events 与 PU_IN 一致
- `panelPageDto.formPages[0].formName: '来料/成品检验单'`，fieldNames = dataSchema 13 字段

### 2.2 dataSchema.fields（13 个表头字段）
单据日期*（日期，defaultValue=today）/ 单据编号*（文本，autoCode）/ 业务类型*（下拉框：来料检验/成品检验——真实 QM15 业务类型）/ 检验类别（下拉框：来料检验/成品检验/首件检验）/ 供应商编码（文本）/ 供应商（参照 PARTNER，refMap 带出 供应商编码；来料检验时填写）/ 仓库（参照 WH）/ 检验员（参照 EMP，真实查询区字段）/ 经手人（参照 EMP）/ 项目（参照 PROJ）/ 部门（参照 DEPT）/ 来源单号（文本，selectConfig headerMap 目标）/ 备注（文本）。

> 参照字段全部走基础档案面板：供应商→PARTNER、检验员/经手人→EMP、项目→PROJ、仓库→WH、部门→DEPT（规范 §2.1B + 开发约束十一-1：能对应基础档案的字段必须引用；下拉选项一律字面量，不引用外部常量）。

### 2.3 detail.tabs[0].fields（18 个明细字段，key=items）
存货编码（文本）/ 存货名称*（参照 INV，refMap 带出 编码/规格/计量单位）/ 规格型号（文本）/ 检验批号（文本，=真实 Batch 批号(合格)）/ 仓库（参照 WH）/ 检验方式*（下拉框 全检/抽检）/ 抽检比例%（小数，默认 100）/ 计量单位*（下拉框 件/kg/套/升）/ 报检数量*（小数）/ 检验数量*（小数）/ 合格数量（小数）/ 不合格数量（小数）/ 合格率（小数 computed）/ 检验结果判定（下拉框 合格/不合格/让步接收/报废，=真实 InspectState）/ 检验员（参照 EMP）/ 检验日期（日期，=真实 检验项目明细 子表 InspectDateTime）/ 检验项目（文本，=真实 InspectProject）/ 判定（下拉框 合格/不合格，=真实 Singlejudgment 单项判定合格）。

**calc 计算链**（合格率 computed，对齐真实 T+ 检验单合格率口径；evaluateExpr 除零保护 b===0→0，草稿行安全）：
```
合格率 = 合格数量 / 检验数量 * 100   （round 2）
```
summaryItems：报检数量合计 / 检验数量合计 / 合格数量合计 / 不合格数量合计。

### 2.4 buttonGroups（18 组，格式 {name, actions}，渲染器只认 actions）
| 组 | actions | 对齐说明 |
|---|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 | 真实 topText |
| 选单 | 选到货单 / 选成品报检单 / 设置默认功能 | 真实 topText（选到货单 走 selectConfig source=ARRIVAL_IN） |
| 修改 | 修改 | 真实独立按钮 |
| 保存 | 保存 / 保存新增 / 保存为草稿 / 保存为常用单据 / 保存打印 / 设置默认功能 | 真实 topText（规范 §2.1B 必含保存组） |
| 删除 | 删除 / 删除单据 | 规范 §12.1 双动作 |
| 审核 | 审核 | 任务指定独立组 |
| 弃审 | 弃审 | 任务指定独立组（真实工具栏含 弃审×2） |
| 审批情况 | 审批情况 | 任务指定独立组 |
| 生单 | 生成采购入库单 / 生成产成品入库单 / 生成委外入库单 / 生成到货退回单 / 生成成品报检退回单（生产报检） / 生成成品报检退回单（委外报检） / 设置默认功能 | 真实 topText（未实现动作保留） |
| 变更 | 变更 | 真实 |
| 生成批号 | 生成明细批号 / 生成不合格品批号 / 设置默认功能 | 真实 topText |
| 查找 | 查找 / 刷新 | 规范要求（真实工具栏无独立查找按钮，按现有面板惯例补） |
| 工具 | 现存量查询 / 变更历史 / 设置默认功能 | 真实 topText |
| 联查 | 到货情况 | 真实 topText（联查 唯一子项） |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置 | 对齐现有面板 |
| 打印 | 直接打印 / 打印 / 预览 / 打印模板设置 / 导出 | 对齐现有面板 |
| 导入 | 下载导入模板 / 导入 | 规范 §18 Excel 导入 |
| 更多 | 复制 / 导出 / 退出 / 放弃 / 草稿 / 附件 / 刷新 / 消息 | 任务指定（复制/导出/退出）+ 规范默认项 |

### 2.5 selectConfig（拉式选单）
- source: `ARRIVAL_IN`（选到货单，仅已审核未中止——交互规范 §3.3/§10 前提；到货单面板为同批并行开发，engine.js 合并后提供）
- headerMap：单据编号→来源单号；detailMap：存货名称→存货名称、到货数量→报检数量、计量单位→计量单位
- **本面板作为选单源**：PURCHASE_IN（采购入库单）工具栏「选单 → 选检验单」即以本面板为数据源（选单弹窗按 `{panelCode:'INSPECTION', condition:{单据状态:'已审核'}}` 拉取，种子含一张已审核单据）；后续「生成采购入库单」生单同样可直接以 INSPECTION 为 source。

---

## 三、种子数据说明（INSPECTION_ROWS，2 张）

| 单据 | 状态 | 业务类型 | 供应商 | 明细 | 用途 |
|---|---|---|---|---|---|
| JY-2026-08-0001（2026-08-14） | 已审核 | 来料检验 | 华东铝业(KH001) | 3 行：铝棒 Φ80(全检200,合格率100) / 6061铝锭(抽检480,合格460,合格率95.83,不合格) / 切削液(全检20,合格18,合格率90,让步接收) | 供 PURCHASE_IN「选检验单」选单源 |
| JY-2026-08-0002（today） | 草稿 | 成品检验 | （无） | 1 行：包装木箱(报检100,未检验) | 演示编辑/审核流 |

- 存货与 INV 种子一致：铝棒 Φ80=CP001 / 6061铝锭=CL002 / 切削液=CL004 / 包装木箱=CL005（对齐 engine.js INV_SEED，保证参照弹窗与存货档案联动）
- 行字段与 dataSchema.fields + detail.tabs[0].fields 完全一致（编号/单据状态/detail.items 数据契约，规范 §二）
- 数值自洽：合格率 = 合格数量/检验数量×100 四舍五入 2 位（460/480×100=95.833…→95.83；18/20×100=90）
- 已审核行带 审批状态=已审批、审核人/审核日期/审核时间、打印次数；草稿行审核字段留空、检验数量 0（calc 除零保护返回 0）

---

## 四、决策点（D1-D7）

- **D1 页签取舍**：真实 QM15 明细页签为 明细/不合格处理 两个。任务仅要求 detail.tabs[0]，且不合格处理子表（不合格原因 MultiUnqualifiedReasonSelect / 处理方式 UnqualifiedTreatType / 报废类型 ScrapType / 报废的结算费用）为附属子表，本次未建第二页签；如需扩展，可仿 PROCESS_REPORT 增加 tabs[1] key='unqualified'，字段从 dom.json 第二组可见列取。
- **D2 检验项目平铺**：真实 T+ 通过「记录样本明细/检验项目」弹窗录入 检验项目明细（编码/项目/内容/标准/结果/时间/单项判定合格）。本配置将 检验项目（InspectProject）、检验日期（InspectDateTime→检验日期）、判定（Singlejudgment→判定）平铺为明细字段，满足任务「明细含 检验项目 相关字段」要求；检验内容/检验标准 未建（弹窗子表内容，任务未要求）。
- **D3 检验类别 vs 检验部门**：真实查询区为 检验部门；任务指定 检验类别（来料检验/成品检验/首件检验），本配置按任务建 检验类别 字段，检验部门 未建（避免与 部门 重复）。
- **D4 数量字段取舍**：应检数量/验收退回数量/让步接收数量/报废数量/不合格原因 为真实可见列但未纳入 18 字段上限（任务列表未列），种子不携带；如需完整复刻可扩展至 23 个明细字段。
- **D5 审核/弃审/审批情况 独立成组**：任务要求 审核/弃审/审批情况 三个独立组（真实工具栏为同一区段），不走审批流（三态直接过审，对齐 PU_IN 决策）。
- **D6 选单源**：selectConfig.source=ARRIVAL_IN（任务指定）；到货单面板为同批并行开发，合并顺序建议 ARRIVAL_IN → INSPECTION（INSPECTION 选单依赖其数据）。
- **D7 编号前缀 JY-**：对齐 §9 单据编号标准；真实 T+ 检验单编码为 QM15 号段，演示环境统一 JY- 前缀（任务指定）。

---

## 五、合并清单（主会话执行，本任务未修改共享代码）

1. **拼入 engine.js**：把 INSPECTION.config.js 内容（`const INSPECTION_CONFIG` + `let INSPECTION_ROWS`）粘贴到 engine.js（建议放 PU_IN_CONFIG 之后）；依赖顶层 `const today`（L54），勿重复声明；删除文件末尾 `module.exports` 行（供验证用）。
2. **注册 panelOf**（L12137 附近）加：`if (panelCode === 'INSPECTION') return { config: INSPECTION_CONFIG, rows: INSPECTION_ROWS }`。
3. **编号前缀登记**：`PxService.generateFormNo`（后端）登记 `JY-`（来料/成品检验单，对齐 §9 单据编号标准：前缀-yyyy-MM-dd+序号）；如走 engine.js mock `nextNoFor`，加 `if (panelCode === 'INSPECTION') { prefix 'JY-' + today + '-'，从 INSPECTION_ROWS 取最大序号 +1 }`。
4. **列表展平**：`flattenFor` 加 INSPECTION 分支（仿 `flattenInvRows`：head + items 展开，`子表数量:1`），供列表按明细行展示。
5. **菜单**：menus.js 挂 `panelCode: 'INSPECTION'`，path `/panelx/list/INSPECTION`（真实菜单码 #QM15 可作菜单备注，title「来料/成品检验单」）。
6. **联动收益**：合并后 engine.js 中已有的「选检验单」（PURCHASE_IN toolbarDiff 选单组）、「生成采购入库单」生单组即具备真实目标面板；INSPECTION「选到货单」依赖 ARRIVAL_IN 面板（同批并行开发）合并后生效。
7. **DB（可选）**：panel_config 表（backend/src/main/resources/db/init.sql）按本配置落库一份（DB 为最终来源，engine.js 仅 mock 兜底）。

---

## 六、注意事项

1. gridTabs 列名 = 明细字段名（规范 §八.8.1 历史事故：列与明细字段对不上会空白）；本配置 gridTabs[0] 18 列、gridTabs[1] 8 列均 ⊆ detail.tabs[0].fields（18 个）已验证。
2. 真实列「供应商(库存)」「BOM版本号」「记录样本明细/样本明细个数」是 T+ 联动/统计列，非核心录入，本配置未建字段定义（列表按文本兜底渲染空值）。
3. 查询区真实为 11 项，本配置 6 项（补 检验类别，去 检验部门/三个自动入库开关/采购订单号/加工单号 到表头或省略），符合任务 5-6 个要求。
4. 审批：任务指定 单据状态 三态（草稿/已审核/已中止），审核组直接过审；若后续要并入审批流，把 INSPECTION 加进 `APPROVAL_PANELS`（engine.js L11434）即可自动补「审批」组与「审批中」状态，无需改本配置。
5. 未实现按钮（引入常用单据/设置默认功能/选成品报检单/生成采购入库单/生成产成品入库单/生成委外入库单/生成到货退回单/生成成品报检退回单（生产报检）/生成成品报检退回单（委外报检）/查找/刷新/导出/退出 等）保留占位，点击走 engine.UNIMPLEMENTED 提示（规范 §三.5），界面与 T+ 保持一致。
6. 界面风格：无卡通/表情/装饰（规范 §四/§14.4 定稿）。
