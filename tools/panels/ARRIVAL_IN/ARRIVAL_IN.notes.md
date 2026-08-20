# 到货单（ARRIVAL_IN / T+ #QM05）面板：真实抓取对照分析 + 完整配置设计

> 对照对象：真实 T+ 机械行业账套 h4t.chanjet.com，单据页 `QM05 到货单`（2026-08-19 抓取，质量管理模块 sysId=QM）
> - 抓取产物：`docs/ref/tplus-live/mech-20260819/qc/arrival_in-list.dom.json`（cols 列定义 / query 查询字段 / toolbar+topText 工具栏下拉全名 / tabs；URL `BAPView/Voucher.aspx?sysId=QM&mId=QM05&pId=voucherView`）
> - 配置模板：`frontend/src/business/engine.js` `PU_ORDER_CONFIG`（L2039 起）/ `PU_IN_CONFIG`（L2184 起，结构：metadata.panelPageDto.tablePages[0].queryFields/gridTabs、formPages、panelButtons、buttonGroups、dataSchema.fields、detail.tabs[0].fields、selectConfig）
> - 规范：`docs/页面开发规范.md`（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名、表尾/固定 5 行/列宽）、`docs/design/面板交互设计规范.md`（§2 表头/§3 选单/§4 工具栏/§8 审批/§9 编号/§12 删除组/§18 导入）
> 本文档为**独立设计产物**，未修改任何共享代码；合并由主会话完成。
> 交付物：ARRIVAL_IN.config.js（engine.js 格式，可直接拼入）/ ARRIVAL_IN.notes.md

---

## 一、真实面板结构提取（机械行业账套 #QM05 到货单）

### 1.1 查询字段（表头字段区，真实 5 项）
`*单据日期 / *单据编号 / *业务类型 / *供应商 / 业务员`
→ 本配置 **6 项**（任务 5-6 个）：单据日期、单据编号、业务类型（普通采购/采购退货）、供应商（参照 PARTNER）、业务员（参照 EMP）、项目（参照 PROJ，补实用查询，参照 PU_IN 补法）。

### 1.2 列表可见列（A 区主明细网格，dom.json cols hidden=false 顺序截取）
| # | 列名（真实） | 真实字段名 | 必填 | 本配置 | 说明 |
|---|---|---|---|---|---|
| 1 | 项目 | Project |  | 保留（参照 PROJ） |  |
| 2 | *存货编码 | Inventory_Code | * | 保留（文本） | 由 存货名称 参照 refMap 带出 |
| 3 | *存货名称 | Inventory | * | 保留（参照 INV，refMap 带出 编码/规格/采购单位） |  |
| 4 | 规格型号 | Inventory_Specification |  | 保留（文本） |  |
| 5 | BOM版本号 | Bom |  | 省略字段定义 | 非核心录入 |
| 6 | 供应商(库存) | DetailPartner |  | 省略 | 供应商已在表头（PARTNER 参照） |
| 7 | 仓库 | Warehouse |  | 保留（参照 WH） |  |
| 8 | *采购单位 | Unit | * | 保留（下拉框 件/kg/套/升） | 由 存货名称 参照 refMap 带出 |
| 9 | 到货数量 | ArrivalQuantity |  | 保留（小数，必填） | 计价基数 |
| 10 | 到货拒收数量 | RejectQuantity |  | 保留（小数） |  |
| 11 | *实收数量 | Quantity | * | 保留（小数，必填） | 检验后实收 |
| 12 | 预计到货日期 | AcceptDate |  | 保留（日期） |  |
| 13 | *检验方式 | InspectType | * | 保留（下拉框 全检/抽检/免检） |  |
| 14 | 抽检比例% | SamplingRatio |  | 保留（小数） |  |
| 15 | 检验要求 | InspectAccessory |  | 保留（文本） | 只入明细字段，不入 gridTabs[0] |
| 16-21 | 累计检验/合格/报废/验收退回/让步接收/入库数量 | CumInspectQuantity 等 6 项 |  | 省略 | 质检累计列，检验单联动后回填，非到货录入 |
| 22 | 不合格原因 | MultiUnqualifiedReasonSelect |  | 省略 | 检验环节回填 |
| 23 | 采购订单号 | PurchaseOrderCode |  | 保留（文本） | 行级来源单号 |
| 24 | 数值公用自定义项1 | pubuserdefdecm1 |  | 省略 | 行业自定义项 |
| 25 | 板号 | pubuserdefnvc2 |  | 省略 | 行业自定义项 |
| 26+ | 单价/含税单价/本币单价/本币含税单价 | OrigDiscountPrice/OrigTaxPrice/DiscountPrice/TaxPrice |  | 单价（小数）/税率%（小数）/含税单价（computed） | 真实网格中价税列隐藏、选单带价；本配置按任务要求补 金额/税额/含税金额 computed（价税分离） |
| 27+ | 货位编码/货位/结存数量/可用数量 等 | InvLocation_Code/StockQuantity/… |  | 省略 | 第二/三组网格为「选存货/现存量查询」附属网格，不在主明细复刻 |
| 28+ | 供应商档案列（简称/结算伙伴/信用额度…） | PartnerAbbName/… |  | 省略 | PARTNER 参照弹窗列，非主网格 |

### 1.3 工具栏（真实分组 + 下拉子项全名，dom.json toolbar/topText）
| 组 | 子项（下拉全名） |
|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 |
| 选单 | 选采购订单 / 选检验单 / 设置默认功能 |
| 保存 | 保存(Alt+S) / 保存新增(Alt+\) / 保存为草稿 / 保存为常用单据 / 保存打印(Alt+G) / 设置默认功能 |
| 修改 | 修改 |
| 删除 | 删除 |
| 审核 | 审核 / 弃审 / 弃审 / 审批情况 |
| 生单 | 生成检验单 / 生成采购入库单 / 设置默认功能 |
| 变更 | 变更 |
| 生成批号 | 生成批号 |
| 工具 | 现存量查询 / 变更历史 / 设置默认功能 |
| 联查 | 采购订单情况 / 检验情况 / 入库情况 / 生单流程联查 / 设置默认功能 |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置 |
| 打印 | 直接打印(Alt+P) / 打印(Alt+;) / 预览(Alt+/) / 打印模板设置(Alt+,) / 导出(Alt+X) / 明细标签打印 / …（topText 截断） |
| 更多 | 更多 / 更多（topText 截断） |

---

## 二、ARRIVAL_IN_CONFIG 设计说明

### 2.1 metadata
- `panelCode: 'ARRIVAL_IN'` / `panelName: '到货单'` / `panelCategory: '单据'`（表尾 备注+审核行 自动显示）
- `autoCodeField: '单据编号'`（标准单据自动编码，前缀 DH- 登记由主会话做）
- `panelState: { dataName: '单据状态', defaultOptions: ['草稿','已审核','已中止'] }`（任务指定三态；未加「审批中」→ 审核走直接过审，不并入 APPROVAL_PANELS 审批流）
- `panelPageDto.tablePages[0]`：
  - queryFields 6 项（见 §一.1）
  - gridTabs[0] 明细 = 18 列（项目/存货编码/存货名称/规格型号/仓库/采购单位/到货数量/到货拒收数量/实收数量/预计到货日期/检验方式/抽检比例%/采购订单号/单价/税率%/含税单价/金额/含税金额）
  - gridTabs[1] 汇总（summary:true）= 9 列（仓库/存货名称/规格型号/采购单位/到货数量/实收数量/单价/金额/含税金额，按 存货名称 分组）
  - **gridTabs 列 ⊆ detail.tabs[0].fields 已程序化验证（node require 校验 gridMiss=[]）**
- `panelPageDto.formPages[0].formName: '到货单'`，fieldNames = dataSchema 14 字段

### 2.2 dataSchema.fields（14 个表头字段）
单据日期*（日期，defaultValue=today）/ 单据编号*（文本，autoCode）/ 业务类型*（下拉框：普通采购/采购退货）/ 供应商编码（文本）/ 供应商*（参照 PARTNER，refMap 带出 供应商编码/供应商简称/结算供应商）/ 供应商简称（文本）/ 仓库（参照 WH）/ 经手人（参照 EMP）/ 业务员（参照 EMP，真实查询区字段）/ 部门（参照 DEPT）/ 项目（参照 PROJ）/ 到货日期（日期）/ 来源单号（文本，承接选单 headerMap 单据编号→来源单号）/ 备注（文本）。

> 参照字段全部走基础档案面板：供应商→PARTNER、经手人/业务员→EMP、项目→PROJ、仓库→WH、部门→DEPT（规范 §2.1B + 开发约束十一-1：能对应基础档案的字段必须引用）。

### 2.3 detail.tabs[0].fields（20 个明细字段，key=items）
项目（参照 PROJ）/ 存货编码（文本）/ 存货名称*（参照 INV，refMap 带出 编码/规格/采购单位）/ 规格型号（文本）/ 仓库*（参照 WH，默认 原料仓）/ 采购单位*（下拉框 件/kg/套/升）/ 到货数量*（小数）/ 到货拒收数量（小数）/ 实收数量*（小数）/ 预计到货日期（日期）/ 检验方式*（下拉框 全检/抽检/免检）/ 抽检比例%（小数）/ 检验要求（文本）/ 采购订单号（文本）/ 单价*（小数）/ 税率%（小数，默认 13）/ 含税单价（computed）/ 金额（computed）/ 税额（computed）/ 含税金额（computed）。

**calc 计算链**（价税分离，对齐真实 T+ 进货单/到货单口径）：
```
含税单价 = 单价 * (1 + 税率% / 100)
金额     = 到货数量 * 单价
税额     = 到货数量 * (含税单价 - 单价)
含税金额 = 金额 + 税额
```
summaryItems：到货数量合计 / 实收数量合计 / 金额合计 / 含税金额合计。

### 2.4 buttonGroups（16 组，格式 {name, actions}，渲染器只认 actions）
| 组 | actions | 对齐说明 |
|---|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 | 真实 topText |
| 选单 | 选采购订单 / 选检验单 / 设置默认功能 | 真实 topText（选采购订单 走 selectConfig 实现） |
| 修改 | 修改 | 真实独立按钮 |
| 保存 | 保存 / 保存新增 / 保存为草稿 / 保存为常用单据 / 保存打印 / 设置默认功能 | 真实 topText（规范 §2.1B 必含保存组） |
| 删除 | 删除 / 删除单据 | 规范 §12.1 双动作 |
| 审核 | 审核 / 弃审 / 审批情况 | 任务指定三态直接过审；审批情况 保留（真实 topText 有） |
| 生单 | 生成检验单 / 生成采购入库单 / 设置默认功能 | 真实 topText（未实现动作保留） |
| 变更 | 变更 | 真实独立按钮 |
| 生成批号 | 生成批号 | 真实独立按钮 |
| 查找 | 查找 / 刷新 | 页面开发规范补 |
| 工具 | 现存量查询 / 变更历史 | 真实 topText（去 设置默认功能 复用项） |
| 联查 | 采购订单情况 / 检验情况 / 入库情况 / 生单流程联查 | 真实 topText |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置 | 对齐现有面板 |
| 打印 | 直接打印 / 打印 / 预览 / 打印模板设置 / 导出 / 明细标签打印 | 真实 topText + 对齐现有面板 |
| 导入 | 下载导入模板 / 导入 | 规范 §18 Excel 导入 |
| 更多 | 复制 / 放弃 / 草稿 / 附件 / 刷新 / 消息 | 规范默认项 |

### 2.5 selectConfig（选单拉式）
- source: `PU_ORDER`（选采购订单，仅已审核未中止——交互规范 §3.3/§10 前提）
- headerMap：单据编号→来源单号、供应商→供应商、项目→项目；detailMap：物料编码/物料名称/规格型号/单位/数量/预计到货日期/单价/税率%/含税单价/金额/含税金额/仓库 → 本面板明细同名字段（数量→到货数量、单位→采购单位）
- **字段名对齐决策（重要）**：任务示例 columns 写 存货名称/采购单位，但 PU_ORDER 明细行真实字段为 物料名称/单位——选单弹窗按来源行字段渲染，对不上会空白（同 gridTabs 空白事故原理），故按 PU_IN 范例对齐为 物料名称/单位 等真实字段（见 §五.1）。
- **本面板作为选单源**：PURCHASE_IN（采购入库单）工具栏「选单 → 选到货单」即以本面板为数据源（选单弹窗按 `{panelCode:'ARRIVAL_IN', condition:{单据状态:'已审核'}}` 拉取，种子含一张已审核单据）；PU_IN 工具栏选单组已含 '选到货单' action（engine.js L2233），合并后即可指向本面板。

---

## 三、种子数据说明（ARRIVAL_IN_ROWS，2 张）

| 单据 | 状态 | 供应商 | 项目 | 明细 | 用途 |
|---|---|---|---|---|---|
| DH-2026-08-0001（2026-08-15） | 已审核 | 华东铝业(KH001) | 铝棒采购/散热片批量 | 3 行：铝棒 Φ80(到货200,拒收2,实收198,金额3100) / 铝板 6061(到货100,金额1280) / 铝型材-散热片(到货300,拒收4,实收296,金额6780) | 供 PURCHASE_IN「选到货单」选单源 |
| DH-2026-08-0002（today） | 草稿 | 中天精工(KH002) | 铝板采购 | 1 行：铝板 6061(到货150,金额1920) | 演示编辑/审核流 |

- 行字段与 dataSchema.fields + detail.tabs[0].fields 完全一致（编号/单据状态/detail.items 数据契约，规范 §二）
- 存货/供应商与 engine.js 既有 MANU_ORDER/SO/PU_IN 种子一致（CP001 铝棒 Φ80、CP002 铝板 6061、CP003 铝型材-散热片；KH001 华东铝业、KH002 中天精工）
- 数值自洽（含税单价=单价×1.13、金额=到货数量×单价、税额=到货数量×(含税单价-单价)、含税金额=金额+税额），与既有种子同口径（教科书四舍五入：15.5×1.13=17.515→17.52）
- 已审核行带 审批状态=已审批、审核人/审核日期/审核时间、打印次数；草稿行审核字段留空

---

## 四、合并接入清单（主会话执行，本任务未修改共享代码）

1. **拼入 engine.js**：把 ARRIVAL_IN.config.js 内容（`const ARRIVAL_IN_CONFIG` + `let ARRIVAL_IN_ROWS`）粘贴到 engine.js（建议放 PU_ORDER_CONFIG 之后）；依赖顶层 `const today`（L54），勿重复声明；**删除末尾 module.exports 行**。
2. **注册 panelOf**（L11473 附近）加：`if (panelCode === 'ARRIVAL_IN') return { config: ARRIVAL_IN_CONFIG, rows: ARRIVAL_IN_ROWS }`。
3. **编号前缀登记**：`PxService.generateFormNo`（后端）登记 `DH-`（到货单，对齐 §9 单据编号标准：前缀-yyyy-MM-dd+序号）；如走 engine.js mock `nextNoFor`（L11630），加 `if (panelCode === 'ARRIVAL_IN') { prefix 'DH-' + today + '-'，从 ARRIVAL_IN_ROWS 取最大序号 +1 }`。
4. **列表展平**：`flattenFor`（L11647）加 ARRIVAL_IN 分支（仿 `flattenInvRows`：head + items 展开），供列表按明细行展示。
5. **菜单**：menus.js 挂 `panelCode: 'ARRIVAL_IN'`，path `/panelx/list/ARRIVAL_IN`（真实菜单码 #QM05 可作菜单备注）。
6. **联动收益**：engine.js 中已有「选到货单」（PU_IN 选单组 L2233）即具备真实目标面板；PURCHASE_IN（invPanel toolbarDiff，L824 选单组）如需「选到货单」在 actions 追加 '选到货单' 即可（选单弹窗 source='ARRIVAL_IN'）。
7. **DB（可选）**：panel_config 表按本配置落库一份；init.sql 种子提示：`form_data` 2 行（panel_code='ARRIVAL_IN'，DH-2026-08-0001/0002，data=表头 JSON、detail_data={"items":[...]}），明细行键与 detail.tabs[0].fields[].dataName 完全一致。

---

## 五、决策点与注意事项

1. **selectConfig 字段名对齐**：任务示例 columns/detailMap 用 存货名称/采购单位/计量单位，但 PU_ORDER 明细行真实字段是 物料名称/单位——选单弹窗按来源行 `row[字段名]` 渲染，字段对不上整列空白（同 §八.8.1 gridTabs 空白事故原理）。本配置对齐为 PU_ORDER 真实字段（columns: 单据编号/单据日期/供应商/币种/汇率/物料名称/数量/单位/预计到货日期；detailMap 数量→到货数量、单位→采购单位），任务指定 headerMap {from:'单据编号',to:'来源单号'} 保留，dataSchema 增加「来源单号」字段承接（种子已带值）。
2. **金额计价基数 = 到货数量**：到货单以到货量确认金额，拒收部分在 采购入库/采购发票 环节冲减；实收数量 保持用户可维护（不设 computed，保留 T+ 检验后调整语义，种子中 实收=到货-拒收 仅作口径演示）。
3. **审批三态**：任务指定 单据状态 三态（草稿/已审核/已中止），审核组用 审核/弃审 直接过审；「审批情况」保留在审核组下拉（真实 topText 有）；若后续要并入审批流，把 ARRIVAL_IN 加进 `APPROVAL_PANELS` 即可自动补「审批」组与「审批中」状态，无需改本配置。
4. **查询区真实 5 项全保留** + 补 项目（参照 PROJ）共 6 项，符合任务 5-6 个要求；供应商编码/应付余额 等真实查询字段未入查询区（已在表头字段，参照 PU_IN 取舍）。
5. **gridTabs 列名 = 明细字段名**（规范 §八.8.1 历史事故：对不上会空白）：gridTabs[0] 18 列、gridTabs[1] 9 列均 ⊆ detail.tabs[0].fields（20 个），已用 node require 程序化验证（gridMiss=[]）。
6. 未实现按钮（引入常用单据/设置默认功能/生成检验单/生成采购入库单/查找/刷新/导出/退出 等）保留占位，点击走 engine.UNIMPLEMENTED 提示（规范 §三.5），界面与 T+ 保持一致。
7. 界面风格：无卡通/表情/装饰（规范 §四/§14.4 定稿）。