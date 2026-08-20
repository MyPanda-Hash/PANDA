# 领料申请单（MATERIAL_REQ）面板设计说明

> 面板码：MATERIAL_REQ / 面板名：领料申请单 / 真实菜单码：#ST1039（sysId=ST, mId=ST1039, pId=voucherView）
> 类型：单据（panelCategory=单据，autoCodeField=单据编号，单据状态：草稿/已审核/已中止）
> 数据来源：真实 T+ h4t 机械行业抓取 `docs/ref/tplus-live/mech-20260819/src/material_req-list.dom.json`（2026-08-19）
> 结构模板：`frontend/src/business/engine.js` 的 `PU_ORDER_CONFIG`（queryFields / gridTabs / formPages / panelButtons / buttonGroups / dataSchema.fields / detail.tabs[0].fields 完全同构，可直接拼入 engine.js）

---

## 一、真实抓取提取结果（material_req-list.dom.json）

### 1. 查询字段（query 区，T+ 表头查询条件，按 label 提取）
- *机构 / *单据日期 / *单据编号 / *业务类型
- 委外供应商 / 仓库 / 生产车间 / 部门 / 领料申请人 / 预计领料日期 / 来源单据 / 来源单号

本项目 queryFields 取 6 个高频查询项：单据日期、单据编号、业务类型、委外供应商（参照 PARTNER）、部门（参照 DEPT）、生产车间（下拉）。

### 2. 明细可见列（cols 区 hidden:false，按真实顺序）
1. 加工单号（ManufactureOrderCode）
2. 产品编码（ProductInventory_Code）
3. 产品名称（ProductInventory）
4. 仓库（Warehouse）
5. *材料名称（Inventory）
6. *计量单位（Unit）
7. *数量（Quantity）
8. 中止数量（ClosedQuantity）
9. 智能选单（RowSelectVoucher）
10. 倒冲料（IsInvertedMaterial）
11. 行中止（IsClose）
12. 累计领料数量（TotalRequisitionedQuantity）
13. 累计调拨数量（TotalTransQuantity）
14. 可用量（AvailableQuantity）
15. 可用量说明（AvailableCompositionQuantity）
16. 现存量（ExistingQuantity）
17. 现存量说明（ExistingCompositionQuantity）
18. 补料新增材料（IsAdditionalMaterial）
19. 数值公用自定义项1（pubuserdefdecm1）
20. 板号（pubuserdefnvc2）
（其余 hidden:true：ID/产品BOM版本号/版本号/项目库存编码/DM/XT/自由项5/地块/颜色/尺码/BOM版本号/批号/生产日期/失效日期/货位/EditState 等）

gridTabs[0].columns 取真实可见列**前 16 个**；gridTabs[1] 汇总按 产品编码/产品名称/材料名称/计量单位/数量/累计领料数量/可用量/现存量 聚合。

### 3. 工具栏（toolbar 区 + topText 下拉全名）
真实工具栏按钮序列：新增 | 选单 | 智能选单 | 选单转换 | 保存 | 修改 | 删除 | 审核 | 弃审 | 审批情况 | 生单 | 变更 | 工具 | 联查 | 设置 | 打印 | 更多 | 智能导入

topText 提取的下拉全名：
- 新增 → 新增/引入常用单据/设置默认功能
- 选单 → 选生产加工单 / 选生产加工单(新增材料) / 选委外加工单 / 设置默认功能
- 保存 → 保存/保存新增/保存为草稿/保存为常用单据/保存打印
- 生单 → 生成材料出库单 / 生成材料出库单(分单) / 生成材料出库单(退料) / 生成委外发料单 / 生成委外发料单(分单) / 生成委外发料单(退料) / 生成调拨单 / 生成调拨单(分单) / 设置默认功能
- 审核组：审核/弃审（面板状态仅 草稿/已审核/已中止，不接审批流，故审核组=审核+弃审）

buttonGroups 最终配置（对齐真实 + 页面开发规范 {name, actions}）：新增/选单/修改/保存/删除/审核(审核,弃审)/生单/变更/查找(查找,刷新)/工具/设置/打印/导入(下载导入模板,导入)/更多(复制,导出,退出)。未实现动作（设置默认功能/导入/导出/复制等）保留按钮项，点击走「演示环境暂未实现」提示（engine.UNIMPLEMENTED）。

---

## 二、配置设计要点

### 1. metadata
- panelCode=MATERIAL_REQ / panelName=领料申请单 / panelCategory=单据 / autoCodeField=单据编号
- panelState：dataName=单据状态，defaultOptions=[草稿, 已审核, 已中止]
- panelPageDto.tablePages[0]：tableName=领料申请单列表；queryFields 6 个；gridTabs=[明细(16列), 汇总(summary:true, 8列)]；topBarBtn=[新增流程, 删除, 刷新]
- formPages[0]：formName=领料申请单；fieldNames 13 个表头字段；底部按钮=[保存, 删除, 审核, 弃审, 放弃]
- panelButtons / buttonGroups 见上；version=1.0

### 2. dataSchema.fields（13 个表头字段，覆盖 单据日期*/单据编号*/业务类型*）
| dataName | dataType | 必填 | 说明 |
|---|---|---|---|
| 单据日期 | 日期 | * | defaultValue=today |
| 单据编号 | 文本 | * | autoCode:true，前缀 LL-（领料），登记由主会话在 PxService.generateFormNo 完成 |
| 业务类型 | 下拉框 | * | 领料申请/委外发料，默认 领料申请 |
| 委外供应商 | 参照 PARTNER | | refField=往来单位名称，filter 停用:false |
| 生产车间 | 下拉框 | | 熔铸/轧制/精整/测试车间 |
| 部门 | 参照 DEPT | | refField=部门名称 |
| 领料申请人 | 参照 EMP | | refField=员工名称 |
| 经手人 | 参照 EMP | | refField=员工名称 |
| 项目 | 参照 PROJ | | refField=项目名称 |
| 仓库 | 参照 WH | | refField=仓库名称，默认 原料仓 |
| 预计领料日期 | 日期 | | |
| 来源单据 | 文本 | | 选单带入（如 生产加工单） |
| 来源单号 | 文本 | | 选单带入 |

参照化约定（开发约束十一-1）：供应商/客户→PARTNER、经手人/领料人→EMP、项目→PROJ、仓库→WH、部门→DEPT、存货物料→INV，全部走参照弹窗拉面板数据，不写死静态 options。

### 3. detail.tabs[0]（key=items，18 个明细字段，对齐真实可见列）
- 字段：加工单号/产品编码(参照INV)/产品名称/材料名称(参照INV,*) /仓库(参照WH,*)/计量单位(下拉,*) /数量(*)/单价/金额(computed)/中止数量/智能选单/倒冲料(是否)/行中止(是否)/累计领料数量/累计调拨数量/可用量(computed)/可用量说明(computed)/现存量(computed)
- summaryItems：数量合计、金额合计
- calc 计算链：
  - 金额 = 数量 × 单价（round 2）
  - 可用量 = 现存量 − 累计领料数量（round 2，领料后剩余可用）
- 产品编码（参照 INV，refMap 存货名称→产品名称 带出）、材料名称（参照 INV）、仓库（参照 WH）按参照弹窗带入，与真实 T+ 存货/仓库档案一致。

### 4. selectConfig（拉式选单）
- source=MANU_ORDER，title=选生产加工单，tip=仅显示已审核且未中止的生产加工单
- 弹窗列：单据编号/单据日期/客户/预完工日/产品名称/数量/生产单位
- detailKey=materials（取加工单材料明细）；headerMap：来源单号、加工单号；detailMap：材料编码→产品编码、材料名称→产品名称/材料名称、计量单位、计划数量→数量、现存量→现存量

### 5. 种子数据（MATERIAL_REQ_ROWS，2 张）
1. **LL-2026-08-0001（已审核）**：熔铸车间领料，项目=铝棒深加工，仓库=原料仓，来源单号 MO-2026-08-1501；明细 3 行（铝棒 Φ80 200kg / 6061铝锭 500kg / 轴套 C 50套），带金额/可用量/现存量——**供材料出库单选领料申请单**（仅已审核可被选）。
2. **LL-2026-08-0002（草稿）**：精整车间委外领料，委外供应商=华东热处理厂，项目=散热片批量；明细 1 行（铝板 6061 100件），金额=0 未审核——供演示新增/修改/审核流转。

字段键与 dataSchema/detail 完全一致（单据日期/单据编号/单据状态/业务类型/…/detail.items），detail 的 key=items 与 detail.tabs[0].key 对齐。

---

## 三、合并清单（由主会话执行，本文件不修改共享代码）
1. `frontend/src/business/engine.js`：`const MATERIAL_REQ_CONFIG = {...}; let MATERIAL_REQ_ROWS = [...]`（本文件内容整体拼入，放在 PU_ORDER 附近即可，依赖 today 已定义于顶部）；
2. `panelOf(panelCode)` 注册：`if (panelCode === 'MATERIAL_REQ') return { config: MATERIAL_REQ_CONFIG, rows: MATERIAL_REQ_ROWS }`；
3. 编号前缀登记：`PxService.generateFormNo` 增加 MATERIAL_REQ → `LL-`（与 SO-/MO-/RK-/CK-/GX- 同规则：前缀-yyyy-MM-dd+2位当日序号）；
4. `menus.js` 菜单挂 `panelCode: 'MATERIAL_REQ'`（真实菜单码 #ST1039，路径 `/panelx/list/MATERIAL_REQ`）；
5. `APPROVAL_PANELS` 不加入（面板状态无 审批中，审核组=审核/弃审）；
6. 验证：`node tools/tplus-grab/verify.cjs --url http://localhost:5173/#/panelx/list/MATERIAL_REQ` 核对列序/合计；`npm run build` 回归。