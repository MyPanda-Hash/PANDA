# 成品报检单（FINISH_INSPECT / T+ #QM10）面板：真实抓取对照分析 + 完整配置设计

| 属性 | 内容 |
|---|---|
| 文档类型 | 场景设计 |
| 适用场景 | 质量与车间 |
| 维护状态 | 调研基线 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../../docs/README.md) |

> 对照对象：真实 T+ 机械行业账套 h4t.chanjet.com，单据页 `QM10 成品报检单`（2026-08-19 抓取）
> - 抓取产物：`docs/ref/tplus-live/mech-20260819/qc/finish_inspect-list.dom.json`（cols 列定义 / query 查询字段 / toolbar+topText 工具栏下拉全名；URL `BAPView/Voucher.aspx?sysId=QM&mId=QM10&pId=voucherView`，rowCount=320 行）
> - 真实菜单树：`docs/ref/tplus-live/mech-20260819/qc-menu-tree.json`（质检管理 → 报检单 → 成品报检单 #QM10；子表 统计表 #QM6010 / 明细表 #QM4010）
> - 配置模板：`tools/panels/PU_IN/PU_IN.config.js`（完整范例）+ `frontend/src/business/engine.js` `PU_ORDER_CONFIG`（结构骨架）、`MANU_ORDER_CONFIG`（detail.tabs[0].key='products'）、`FINISH_IN_CONFIG`（selectConfig 同款：source:'MANU_ORDER'、detailKey:'products'）
> - 规范：`docs/页面开发规范.md`（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名、参照字段规范化）、`docs/design/面板交互设计规范.md`（§2 表头/§3 选单/§4 工具栏/§9 编号/§12 删除组）
> 本文档为**独立设计产物**，未修改任何共享代码；合并由主会话完成。
> 交付物：FINISH_INSPECT.config.js（engine.js 格式，可直接拼入）/ FINISH_INSPECT.notes.md

---

## 一、真实面板结构提取（机械行业账套 #QM10 成品报检单）

### 1.1 查询字段（表头字段区，真实 7 项）
`*单据日期 / *单据编号 / *业务类型 / 供应商编码 / *委外供应商 / 部门 / 负责人·业务员`
→ 本配置 **6 项**（任务 3-6 个）：单据日期、单据编号、业务类型（完工报检/退库报检）、报检类别（自制/委外加工报检，补实用查询）、生产车间（参照 DEPT）、仓库（参照 WH）。

### 1.2 列表可见列（dom.json cols hidden=false，主网格 A 区顺序）
| # | 列名（真实） | 真实字段名 | 本配置处理 |
|---|---|---|---|
| 1 | *存货名称 | Inventory | 产品名称（参照 INV，refMap 带出 编码/规格/计量单位/图号） |
| 2 | 规格型号 | Inventory_Specification | 保留（文本） |
| 3 | BOM版本号 | Bom | 省略字段定义（种子行不携带） |
| 4 | 供应商(库存) | DetailPartner | 省略字段定义（委外报检按表头 报检类别 区分） |
| 5 | 仓库 | Warehouse | 保留（参照 WH） |
| 6 | *生产单位 | Unit | 计量单位（下拉框 件/kg/套/升） |
| 7 | *报检数量 | Quantity | 保留（小数，必填） |
| 8-13 | *单价/*税率%/*含税单价/*金额/*税额/*含税金额 | OrigDiscountPrice/TaxRate/… | 省略字段定义（报检单不录价，价税列属 T+ 存货参照网格带出） |
| 14 | 预完工日 | PreFinishDate | 保留（日期） |
| 15 | *检验方式 | InspectType | 保留（下拉框 全检/抽检） |
| 16 | 抽检比例% | SamplingRatio | 保留（小数） |
| 17 | 检验要求 | InspectAccessory | 保留（文本） |
| 18 | 班组 | ProcessTeam | 保留（下拉框，对齐 MANU_ORDER 班组选项） |
| 19 | 工人 | Person | 保留（文本） |
| 20-26 | 累计检验/合格/验收退回/让步接收/报废/入库/已退数量 | Cum*Quantity | 省略字段定义（执行结果列，检验单/执行表负责回写） |
| 27 | 不合格原因 | MultiUnqualifiedReasonSelect | 省略字段定义（本面板检验前录入，不合格原因由检验单维护） |

> 后续网格组为附属参照（选存货：存货名称/数量/单价/金额…；选仓库货位：货位/结存/可用数量…；往来单位选单），不在主网格复刻。
> 报检检验核心 4 列（合格数量/不合格数量/检验结果/检验员）在真实列表中以累计列体现，本配置按任务要求以**明细行录入**建模（合格数量/不合格数量/检验结果/检验员），不合格数量走 calc = 报检数量 - 合格数量。

### 1.3 工具栏（真实分组 + 下拉子项全名，dom.json toolbar/topText）
| 组 | 子项（下拉全名） |
|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 |
| 选单 | 选生产加工单 / 选委外加工单 / 选检验单 / 设置默认功能 |
| 保存 | 保存(Alt+S) / 保存新增(Alt+\) / 保存为草稿 / 保存为常用单据 / 保存打印(Alt+G) / 设置默认功能 |
| 修改 | 修改 |
| 删除 | 删除 |
| 审核 | 审核 / 弃审 / 弃审 / 审批情况（顶层独立按钮） |
| 生单 | 生成检验单 / 生成产成品入库单 / 生成委外入库单 / 设置默认功能 |
| 变更 | 变更 |
| 生成批号 | 生成批号 |
| 工具 | 现存量查询 / 变更历史 / 设置默认功能 |
| 联查 | 生产加工情况 / 委外加工情况 / 检验情况 / 产品入库情况 / 委外入库情况 / 装箱情况 / 生单流程联查 |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置（topText 尾部截断补齐） |
| 打印 | 打印 |
| 更多 | 更多 |

### 1.4 业务类型 / 报检类别 决策点
- 真实查询区只有 `*业务类型` 与 `*委外供应商`（委外报检才选供应商），topText 选单同时提供 选生产加工单/选委外加工单，生单同时提供 生成委外入库单。
- 结论：业务类型 = **完工报检 / 退库报检**（报检单典型两分，任务指定）；报检类别 = **自制加工报检 / 委外加工报检**（对齐 选单/生单 双通道与 委外供应商 字段存在的前提）。委外供应商本身不在本配置表头建模（由报检类别+联查委外加工情况表达，避免无基础档案参照面板空转）。

---

## 二、FINISH_INSPECT_CONFIG 设计说明

### 2.1 metadata
- `panelCode: 'FINISH_INSPECT'` / `panelName: '成品报检单'` / `panelCategory: '单据'`
- `autoCodeField: '单据编号'`（标准单据自动编码，前缀 BJ- 登记由主会话做）
- `panelState: { dataName: '单据状态', defaultOptions: ['草稿','已审核','已中止'] }`（任务指定三态；未加「审批中」→ 审核走直接过审，不并入 APPROVAL_PANELS 审批流）
- `panelPageDto.tablePages[0]`：
  - tableName '成品报检单列表'，queryFields 6 项（见 §一.1）
  - gridTabs[0] 明细 = 真实可见列 16 个（产品名称/存货编码/规格型号/计量单位/报检数量/合格数量/不合格数量/检验结果/检验员/检验方式/抽检比例%/检验要求/班组/工人/仓库/图号）
  - gridTabs[1] 汇总（summary:true）= 仓库/产品名称/规格型号/计量单位/报检数量/合格数量/不合格数量/检验结果（按 产品名称 分组）
- `panelPageDto.formPages[0].formName: '成品报检单'`，fieldNames = dataSchema 11 字段

### 2.2 dataSchema.fields（11 个表头字段）
单据日期*（日期，defaultValue=today）/ 单据编号*（文本，autoCode）/ 业务类型*（下拉框：完工报检/退库报检）/ 报检类别（下拉框：自制加工报检/委外加工报检）/ 生产车间（参照 DEPT）/ 加工单号（文本，displayName=生产加工单号）/ 仓库（参照 WH）/ 经手人（参照 EMP）/ 项目（参照 PROJ）/ 报检说明（文本）/ 备注（文本）。

> 参照字段全部走基础档案面板：生产车间→DEPT、仓库→WH、经手人→EMP、项目→PROJ（规范 §2.1B + 开发约束十一-1：能对应基础档案必须引用；下拉选项一律字面量）。

### 2.3 detail.tabs[0].fields（18 个明细字段，key=items）
产品名称*（参照 INV，refMap 带出 存货编码/规格型号/计量单位/图号）/ 存货编码（文本）/ 规格型号（文本）/ 计量单位*（下拉框 件/kg/套/升）/ 报检数量*（小数）/ 合格数量（小数，默认 0）/ 不合格数量（computed）/ 检验结果（下拉框 待检/合格/不合格/让步接收，默认 待检）/ 检验员（参照 EMP）/ 检验方式*（下拉框 全检/抽检）/ 抽检比例%（小数）/ 检验要求（文本）/ 班组（下拉框，对齐 MANU_ORDER）/ 工人（文本）/ 图号（文本）/ 仓库（参照 WH，默认 成品仓）/ 预完工日（日期）/ 现存量（computed）。

**calc 计算链**：`不合格数量 = 报检数量 - 合格数量`（round 2）。summaryItems：报检数量合计 / 合格数量合计 / 不合格数量合计。

### 2.4 buttonGroups（16 组，格式 {name, actions}，渲染器只认 actions）
| 组 | actions | 对齐说明 |
|---|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 | 真实 topText |
| 选单 | 选生产加工单 / 选委外加工单 / 选检验单 / 设置默认功能 | 真实 topText（选生产加工单 走 selectConfig 实现） |
| 修改 | 修改 | 真实独立按钮 |
| 保存 | 保存 / 保存新增 / 保存为草稿 / 保存为常用单据 / 保存打印 / 设置默认功能 | 真实 topText（规范 §2.1B 必含保存组） |
| 删除 | 删除 / 删除单据 | 规范 §12.1 双动作 |
| 审核 | 审核 | 真实顶层按钮（任务分组） |
| 弃审 | 弃审 | 真实顶层按钮（真实工具栏出现两次，演示取一次；不走审批流，见 §五.4） |
| 审批情况 | 审批情况 | 真实顶层按钮 |
| 生单 | 生成检验单 / 生成产成品入库单 / 生成委外入库单 / 设置默认功能 | 真实 topText（未实现动作保留） |
| 变更 | 变更 | 真实 |
| 生成批号 | 生成批号 | 真实 |
| 工具 | 现存量查询 / 变更历史 | 真实 工具组 |
| 联查 | 生产加工情况 / 委外加工情况 / 检验情况 / 产品入库情况 / 委外入库情况 / 装箱情况 / 生单流程联查 | 真实 联查组 |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置 | 对齐现有面板 |
| 打印 | 直接打印 / 打印 / 预览 / 打印模板设置 / 导出 | 对齐现有面板 |
| 更多 | 复制 / 导出 / 退出 / 放弃 / 草稿 / 附件 / 刷新 / 消息 | 任务指定 + 规范默认项 |

### 2.5 selectConfig（拉式选单 + 本面板为选单源）
- source: `MANU_ORDER`（选生产加工单，仅已审核未中止——交互规范 §3.3/§10 前提）；detailKey: 'products'（MANU_ORDER 产成品明细 tab key）
- headerMap：单据编号→加工单号（表头字段 dataName='加工单号'、displayName='生产加工单号'，见 §五.2）；detailMap：产品名称/规格型号/生产单位→计量单位/数量→报检数量/现存量
- **本面板作为选单源**：产成品入库单（FINISH_IN，真实 topText「选单 → 选成品报检单」）与检验单（真实 topText「选单 → 选成品报检单」）以本面板为数据源（选单弹窗按 `{panelCode:'FINISH_INSPECT', condition:{单据状态:'已审核'}}` 拉取，故种子含一张已审核单据）；生产加工单「生单 → 生成成品报检单」为本面板的推式来源。

---

## 三、种子数据说明（FINISH_INSPECT_ROWS，2 张，前缀 BJ-）

| 单据 | 状态 | 业务类型/报检类别 | 生产车间 | 来源加工单 | 明细 | 用途 |
|---|---|---|---|---|---|---|
| BJ-2026-08-0001（2026-08-18） | 已审核 | 完工报检/自制加工报检 | 熔铸车间 | MO-2026-08-0009 | 3 行：铝棒 Φ80(200件,合格198) / 铝板 6061(100件,合格100) / 减速箱体 A(50件,让步接收) | 供 FINISH_IN/检验单「选成品报检单」选单源 |
| BJ-2026-08-0002（today） | 草稿 | 退库报检/委外加工报检 | 精整车间 | （未选单） | 1 行：铝型材-散热片(300件,待检) | 演示编辑/审核流 |

- 行字段与 dataSchema.fields + detail.tabs[0].fields 完全一致（编号/单据状态/detail.items 数据契约，规范 §二）
- 数量自洽：不合格数量 = 报检数量 - 合格数量（198+2=200、100+0=100、48+2=50），与 calc 链同口径
- 存货与 INV 种子一致（CP001 铝棒 Φ80 / CP002 铝板 6061 / CP003 铝型材-散热片 / CP004 减速箱体 A，engine.js INV seed 与 MANU_ORDER 产成品明细同名同码）
- 已审核行带 审批状态=已审批、审核人/审核日期/审核时间、打印次数；草稿行审核字段留空

---

## 四、合并清单（主会话执行，本任务未修改共享代码）

1. **拼入 engine.js**：把 FINISH_INSPECT.config.js 内容（`const FINISH_INSPECT_CONFIG` + `let FINISH_INSPECT_ROWS`）粘贴到 engine.js（建议放 PU_IN_CONFIG 之后）；依赖顶层 `const today`（L54），本文件已用 `__FINISH_INSPECT_TODAY` 守卫兼容独立 require，合并后无需改动。
2. **注册 panelOf**（engine.js L11473 附近）加：`if (panelCode === 'FINISH_INSPECT') return { config: FINISH_INSPECT_CONFIG, rows: FINISH_INSPECT_ROWS }`。
3. **编号前缀登记**：`PxService.generateFormNo`（后端）登记 `BJ-`（成品报检单，对齐 §9 单据编号标准：前缀-yyyy-MM-dd+序号）；如走 engine.js mock `nextNoFor`（L11630），加 `if (panelCode === 'FINISH_INSPECT') { prefix 'BJ-' + today + '-'，从 FINISH_INSPECT_ROWS 取最大序号 +1 }`。
4. **列表展平**：`flattenFor`（L11647）加 FINISH_INSPECT 分支（仿 `flattenInvRows`：head + items 展开，`子表数量:1`），供列表按明细行展示。
5. **菜单**：menus.js 挂 `panelCode: 'FINISH_INSPECT'`，path `/panelx/list/FINISH_INSPECT`。建议新增一级菜单「质检管理」（真实菜单树：质检管理 → 报检单 → 成品报检单 #QM10），或先挂 生产管理 → 单据（MANU_ORDER 旁，`operationName: '新增流程'`）。
6. **联动收益**：合并后 engine.js 中已有的「选成品报检单」（FINISH_IN 选单组、检验单选单组，真实 topText 均含此项）即具备真实目标面板；MANU_ORDER「生单 → 生成成品报检单」的推式实现可直接以 FINISH_INSPECT 为目标。
7. **DB（可选）**：`backend/src/main/resources/db/init.sql` 的 panel_config / menu 表按本配置落库一份（DB 为最终来源，engine.js 仅 mock 兜底）。

---

## 五、注意事项

1. gridTabs 列名 = 明细字段名（规范 §八.8.1 历史事故：列与明细字段对不上会空白）；本配置 gridTabs[0] 16 列、gridTabs[1] 8 列均 ⊆ detail.tabs[0].fields（18 个），已用 require 断言脚本验证通过。
2. **加工单号 字段命名**：任务 selectConfig.headerMap 目标为 `加工单号`，表头列表写作 `生产加工单号`；PanelxForm.vue confirmSelect（L403-408）按 `form[m.to] = first[m.from]` 回填，目标必须等于真实 dataSchema 字段名才能渲染。故字段 `dataName: '加工单号'` + `displayName: '生产加工单号'`（buildMeta L12148 `name: f.displayName || f.dataName`），两处要求同时满足。
3. 真实列价税系列（单价/税率%/含税单价/金额/税额/含税金额）与累计系列（累计检验/合格/退回/让步/报废/入库/已退数量）在报检单中为存货参照/执行结果列，非检验前录入项，本配置省略字段定义（列表按文本兜底）；报检检验核心 合格/不合格/检验结果/检验员 在明细行建模。
4. 审批：任务指定 单据状态 三态（草稿/已审核/已中止），审核/弃审 直接过审；若后续要并入审批流，把 FINISH_INSPECT 加进 `APPROVAL_PANELS`（engine.js L11434）即可自动补「审批」组与「审批中」状态，无需改本配置。
5. 未实现按钮（引入常用单据/设置默认功能/生成检验单/生成产成品入库单/生成委外入库单/变更/生成批号/审批情况/现存量查询/变更历史/联查/设置/导出/退出 等）保留占位，点击走 engine.UNIMPLEMENTED 提示（规范 §三.5），界面与 T+ 保持一致。
6. 界面风格：无卡通/表情/装饰（规范 §四/§14.4 定稿）。
