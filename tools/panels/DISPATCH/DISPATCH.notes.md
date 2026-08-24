# 工序派工单（DISPATCH / T+ #SW10）面板：真实抓取对照分析 + 完整配置设计

| 属性 | 内容 |
|---|---|
| 文档类型 | 场景设计 |
| 适用场景 | 质量与车间 |
| 维护状态 | 调研基线 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../../docs/README.md) |

> 对照对象：真实 T+ 机械行业账套 h4t.chanjet.com，单据页 `SW10 工序派工单`（2026-08-19 抓取）
> - 抓取产物：`docs/ref/tplus-live/mech-20260819/qc/dispatch-list.dom.json`（cols 246 列 / query 6 项 / toolbar+topText 工具栏下拉全名 / strips；rowCount=465，URL `BAPView/Voucher.aspx?sysId=SW&mId=SW10`）
> - 配置模板：`frontend/src/business/engine.js` `PU_ORDER_CONFIG`（L2041 单据面板模板）+ `MANU_ORDER_CONFIG`（L59 生产加工单，其 工序明细 为本面板选单来源）+ `PROCESS_REPORT_CONFIG`（L2849 同属工序级单据：工序编码/工序名称/生产车间/设备/工人 字段参照）
> - 规范：`docs/页面开发规范.md`（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名）、`docs/design/面板交互设计规范.md`（§2 表头/§3 选单/§4 工具栏/§8 审批/§9 编号/§12 删除组/§18 导入）
> 本文档为**独立设计产物**，未修改任何共享代码；合并由主会话完成。
> 交付物：DISPATCH.config.js（engine.js 格式，可直接拼入）/ DISPATCH.notes.md

---

## 一、真实面板结构提取（机械行业账套 #SW10 工序派工单）

### 1.1 查询字段（表头字段区，真实 6 项）
`*单据日期 / *单据编号 / 部门 / 经手人 / 预开工日 / 预完工日`
→ 本配置按任务指定 **6 项**：单据日期、单据编号、业务类型（工序派工/委外派工）、生产车间（参照 DEPT）、工序（参照 OP）、设备（参照 EQUIP）；部门/经手人/预开工日/预完工日 均保留在 dataSchema 表头字段（§五.1）。

### 1.2 列表可见列（dom.json cols hidden=false 顺序截取，A 区主网格 39 列）
| 列名（真实） | 真实字段名 | 本配置 |
|---|---|---|
| *加工单号 | ManufactureOrderCode | 表头 加工单号（文本，选单 headerMap 带出） |
| *产品名称 | Inventory | 明细 产品名称（文本）+ 表头 产品名称 |
| 产品图片 | Inventory_ImageFile | 省略（图片列，规范 §四 不入字段） |
| 规格型号 | Inventory_Specification | 明细 规格型号（文本） |
| 图纸 | BluePrint | 省略（附件按钮列） |
| 工艺要求 | TechRequirements | 省略（真实列；OP 档案备注可带入，见 §五.4） |
| *加工类型 | ProcessingType | 明细 加工类型*（下拉框 自制/委外） |
| *工序名称 | Process | 明细 工序名称（文本，OP 参照带出） |
| 单位标准工时 | StandardTime | 省略（选单 detailMap 链路保留） |
| 工序加工要求 | ProcessRequirement | 省略（真实列，见 §五.4） |
| 末工序 / 需要转移 | IsEndProcess / IsNeedTransfer | 省略（布尔列） |
| 已派工数量 | TotalDispatchQuantity | 明细 已派工数量（小数） |
| *派工数量 | Quantity | 明细 派工数量*（小数，必填） |
| 生产车间 | WorkShop | 明细 生产车间（参照 DEPT）+ 表头 |
| 工作中心 | WorkCenter | 明细 工作中心（参照 WC） |
| 班组 | ProcessTeam | 明细 班组（参照 TEAM） |
| 班组成员 | TeamPerson | 省略（班组子表语义） |
| 工人 | Person | 明细 工人（参照 EMP） |
| 设备 | Equipment | 明细 设备（参照 EQUIP） |
| 委外供应商 | Partner | 明细 委外供应商（参照 PARTNER） |
| 本币委外单价 / 本币委外金额 | DiscountPrice / DiscountAmount | 省略（委外金额体系，见 §五.3） |
| *预开工日 / *预完工日 | PreStartDate / PreFinishDate | 明细 预开工日/预完工日（日期）+ 表头 |
| 开工日期/开工时间/完工日期/完工时间 | StartDate/StartTime/FinishDate/FinishTime | 省略（执行日期，汇报环节回写） |
| 派工加工状态 | FinishState | 明细 派工加工状态（下拉框 未派工/已派工/完工） |
| 手工完工 | IsManualProcessFinish | 省略（布尔） |
| 备注 | Memo | 明细 备注（文本）+ 表头 |
| 派工行码 | BarCode | 省略（T+ 行码生成列） |
| 累计汇报/合格/不合格数量 | TotalReportQuantity / TotalQualifiedQuantity / TotalUnQualifiedQuantity | 明细 累计汇报数量（小数）；合格/不合格 省略 |
| 销售订单号 / 客户 | SaleOrderCode / Customer | 省略表头（销售订单号 保留在选单带出链路） |
| 数值公用自定义项1 | pubuserdefdecm1 | 省略（行业自定义项） |

> 第二组可见列（加工单号/产品名称/工序名称/派工数量…）为「选生产加工单-工序」参照网格；第三组 材料明细（*材料名称/需用数量/计划数量/替代件…）、本工序材料（*材料编码/材料名称/*计划数量）、班组成员（*工人编码/*工人名称/能力权重系数/工时权重系数/*计件分配比例%）为附属子表，不在主网格复刻。

### 1.3 工具栏（真实 toolbar/topText）
| 组 | 子项（下拉全名） |
|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 |
| 选单 | 选生产加工单-工序 / 选生产加工单-材料 / 选生产加工单-增加联副产品 / 设置默认功能 |
| 保存 | 保存(Alt+S) / 保存新增(Alt+\) / 保存为草稿 / 保存打印(Alt+G) / 设置默认功能 |
| 修改 | 修改 |
| 删除 | 删除 |
| 审核 | 弃审 / 弃审 / 审核 / 审批情况 |
| 生单 | 生成材料出库单 / 生成材料出库单(分单) / 生成材料出库单（退料） / 生成工序汇报单（自制汇报） / 生成工序汇报单（委外汇报） / 生成工序汇报单（自制汇报退回） / 生成工序汇报单（委外汇报退回） / 设置默认功能 |
| 变更 | 变更 |
| 中止 | 中止 / 中止（释放未执行量） / 设置默认功能 |
| 取消中止 | 取消中止（独立按钮） |
| 工具 | topText 截断，按同族单据补齐 现存量查询 / 变更历史 / 生单流程联查 |
| 联查 | 联查（独立按钮，补 执行情况 / 派工情况） |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置 |
| 打印 | 打印（补 直接打印 / 预览 / 打印模板设置 / 工序流转卡打印 / 导出） |
| 更多 | 更多（topText 截断，按规范默认项 复制 / 放弃 / 草稿 / 附件 / 刷新 / 消息） |

---

## 二、DISPATCH_CONFIG 设计说明

### 2.1 metadata
- `panelCode: 'DISPATCH'` / `panelName: '工序派工单'` / `panelCategory: '单据'`（表尾 备注+审核行 自动显示）
- `autoCodeField: '单据编号'`（标准单据自动编码，前缀 PG- 登记由主会话做，见 §四.3）
- `panelState: { dataName: '单据状态', defaultOptions: ['草稿','已审核','已中止'] }`（任务指定三态；未加「审批中」→ 审核走直接过审，不并入 APPROVAL_PANELS 审批流）
- `panelPageDto.tablePages[0]`：
  - queryFields 6 项（见 §一.1）
  - gridTabs[0] 明细 = 真实可见列 18 个（产品名称/工序名称/生产车间/工作中心/设备/班组/工人/加工类型/计划数量/已派工数量/派工数量/计量单位/预开工日/预完工日/派工加工状态/累计汇报数量/规格型号/备注）
  - gridTabs[1] 汇总（summary:true）= 工序名称/生产车间/工作中心/班组/计量单位/计划数量/已派工数量/派工数量/累计汇报数量（按 工序名称 分组）
- `panelPageDto.formPages[0].formName: '工序派工单'`，fieldNames = dataSchema 12 字段
- **中止/取消中止**：真实工具栏独立按钮，panelButtons 与 bottomOperationBarBtn 均含（任务指定）

### 2.2 dataSchema.fields（12 个表头字段）
单据日期*（日期，defaultValue=TODAY）/ 单据编号*（文本，autoCode）/ 业务类型*（下拉框 工序派工/委外派工）/ 生产车间*（参照 DEPT，默认 熔铸车间）/ 加工单号（文本）/ 产品名称（文本）/ 预开工日（日期）/ 预完工日（日期）/ 经手人（参照 EMP）/ 项目（参照 PROJ）/ 部门（参照 DEPT）/ 备注（文本）。

> 参照字段规范化（任务指定）：生产车间/部门→DEPT、经手人→EMP、项目→PROJ；DEPT 档案（engine.js BASE_CONFIGS）已含 D05 熔铸车间/D06 轧制车间/D07 精整车间/D08 测试车间，defaultValue 熔铸车间 可命中。下拉选项一律字面量（规范 §2.1B + 开发约束十一-1）。

### 2.3 detail.tabs[0].fields（20 个明细字段，key=items）
工序编码*（参照 OP，refMap 带出 工序名称/生产车间）/ 工序名称（文本）/ 产品名称（文本）/ 生产车间（参照 DEPT，默认 熔铸车间）/ 工作中心（参照 WC）/ 设备（参照 EQUIP）/ 班组（参照 TEAM）/ 工人（参照 EMP）/ 加工类型*（下拉框 自制/委外）/ 计划数量（小数）/ 已派工数量（小数）/ 派工数量*（小数）/ 计量单位*（下拉框 件/kg/套/升）/ 预开工日（日期）/ 预完工日（日期）/ 派工加工状态（下拉框 未派工/已派工/完工）/ 累计汇报数量（小数）/ 委外供应商（参照 PARTNER）/ 规格型号（文本）/ 备注（文本）。

summaryItems：计划数量合计 / 已派工数量合计 / 派工数量合计 / 累计汇报数量合计。

### 2.4 buttonGroups（16 组，格式 {name, actions}，渲染器只认 actions）
| 组 | actions | 对齐说明 |
|---|---|---|
| 新增 | 新增 / 引入常用单据 / 设置默认功能 | 真实 topText |
| 选单 | 选生产加工单-工序 / 选生产加工单-材料 / 选生产加工单-增加联副产品 / 设置默认功能 | 真实 topText（选生产加工单-工序 走 selectConfig 实现） |
| 保存 | 保存 / 保存新增 / 保存为草稿 / 保存打印 / 设置默认功能 | 真实 topText（规范 §2.1B 必含保存组） |
| 修改 | 修改 | 真实独立按钮 |
| 删除 | 删除 / 删除单据 | 规范 §12.1 双动作 |
| 审核 | 审核 / 弃审 | 真实（真实另有 弃审×2/审批情况，见下组） |
| 审批情况 | 审批情况 | 真实独立按钮（任务指定成组） |
| 生单 | 生成材料出库单 / 生成材料出库单(分单) / 生成材料出库单（退料） / 生成工序汇报单（自制汇报） / 生成工序汇报单（委外汇报） / 生成工序汇报单（自制汇报退回） / 生成工序汇报单（委外汇报退回） / 设置默认功能 | 真实 topText（未实现动作保留） |
| 变更 | 变更 | 真实 |
| 中止 | 中止 / 中止（释放未执行量） / 设置默认功能 | 真实 topText（任务指定成组） |
| 取消中止 | 取消中止 | 真实独立按钮（任务指定成组） |
| 工具 | 现存量查询 / 变更历史 / 生单流程联查 | 同族单据补齐 |
| 联查 | 执行情况 / 派工情况 / 联查 | 真实独立按钮，补执行/派工情况 |
| 设置 | 单据设置 / 移动控件位置 / 调整控件宽度 / 工具栏设置 | 对齐现有面板 |
| 打印 | 直接打印 / 打印 / 预览 / 打印模板设置 / 工序流转卡打印 / 导出 | 对齐现有面板 |
| 更多 | 复制 / 放弃 / 草稿 / 附件 / 刷新 / 消息 | 规范默认项 |

### 2.5 selectConfig（选单拉式，选单源单据面板）
- source: `MANU_ORDER`（选生产加工单-工序，仅已审核未中止——交互规范 §3.3/§10 前提）
- headerMap：单据编号→加工单号、预开工日→预开工日、预完工日→预完工日、生产车间→生产车间
- detailMap：产品名称/规格型号/工序编码/工序名称/生产车间/工作中心/设备/班组/工人/加工类型/计划数量/工序单位→计量单位/委外供应商 → 本面板明细同名字段
- detailRows：对齐 PROCESS_REPORT——工序行本身不含产品信息，从 MANU_ORDER 单据头/产成品明细行补齐 加工单号/产品名称/规格型号（合并后引用 engine.js 顶层 MOCK_ROWS）

---

## 三、种子数据说明（DISPATCH_ROWS，2 张）

| 单据 | 状态 | 来源加工单 | 明细（工序 / 计划/已派/派工） | 用途 |
|---|---|---|---|---|
| PG-2026-08-0001（2026-08-14） | 已审核 | MO-2026-08-0001 铝棒 Φ80（华东铝业） | 3 行：下料 PX001(300/300/300) / 熔铸 PX010(300/200/200) / 挤压 PX011(300/150/150) | 供下游 材料出库「选工序派工单」/工序汇报 等作选单源 |
| PG-2026-08-0002（today） | 草稿 | MO-2026-08-0002 铝型材-散热片（西部材料） | 2 行：时效 PX012 / 精整 PX013（均 300/0/0，未派工） | 演示编辑/审核流 |

- 工序数据与真实 T+ 工序一致：下料/熔铸/挤压/时效/精整；车间 熔铸车间/轧制车间/精整车间；工人 张伟/李娜/王强/赵敏（任务指定）
- 行字段与 detail.tabs[0].fields 完全一致（编号/单据状态/detail.items 数据契约，规范 §二）
- 数值自洽：已派工数量 ≤ 计划数量、派工数量 ≤ 计划数量；已派工行 派工加工状态=已派工
- 已审核行带 审批状态=已审批、审核人/审核日期/审核时间、打印次数；草稿行审核字段留空

---

## 四、合并清单（主会话执行，本任务未修改共享代码）

1. **拼入 engine.js**：把 DISPATCH.config.js 内容（`const DISPATCH_CONFIG` + `let DISPATCH_ROWS`）粘贴到 engine.js（建议放 PROCESS_REPORT_CONFIG 之后）；**删除末尾 `module.exports` 行**；依赖顶层 `const today`（L54），本文件用 TODAY 兼容，合并后自动沿用顶层 today，勿重复声明。
2. **注册 panelOf**（L12128 附近）加：`if (panelCode === 'DISPATCH') return { config: DISPATCH_CONFIG, rows: DISPATCH_ROWS }`。
3. **编号前缀登记**：`PxService.generateFormNo`（后端）登记 `PG-`（工序派工，对齐 §9 单据编号标准：前缀-yyyy-MM-dd+序号）；engine.js mock `nextNoFor`（L12297）加 `if (panelCode === 'DISPATCH') { prefix 'PG-' + today + '-'，从 DISPATCH_ROWS 取最大序号 +1（padStart 4）}`。
4. **列表展平**：`flattenFor`（L12314）加 DISPATCH 分支（仿 `flattenProcessReportRows`：head + items 展开，`子表数量:1`），供列表按明细行展示。
5. **单据级行处理**：`VOUCHER_CODES`（L12424）加入 `'DISPATCH'`（单据类面板返回单据级行（带 detail））。
6. **菜单**：menus.js 生产菜单「工序汇报单」（L135 附近）后挂 `{ code: 'dispatch', title: '工序派工单', path: '/panelx/list/DISPATCH', icon: 'EditPen', panelCode: 'DISPATCH', operationName: '新增流程' }`（真实菜单码 #SW10 可作菜单备注）。
7. **DB（可选）**：init.sql `panel_config` 表按本配置落库一份（仿 L155 PROCESS_REPORT 行）：`INSERT INTO panel_config (panel_code, panel_name, category, config) VALUES ('DISPATCH', '工序派工单', '单据', '{"metadata":{...}}')`（DB 为最终来源，engine.js 仅 mock 兜底）。
8. **联动收益**（合并后既有引用即具备真实目标面板）：
   - MANU_ORDER 工具栏「选工序派工单」子项（engine.js L1732）→ 选单源指向 DISPATCH（仅已审核未中止）；
   - MATERIAL_OUT（材料出库单）真实选单组含「选工序派工单」（MATERIAL_OUT.design.md/select.md 判定缺面板）→ 挂 source:'DISPATCH'；
   - 本面板 生单组「生成工序汇报单（自制/委外汇报）」→ 目标面板 PROCESS_REPORT（已存在）。

---

## 五、决策点与注意事项

1. **queryFields 取舍**：真实查询区 6 项为 单据日期/单据编号/部门/经手人/预开工日/预完工日；按任务指定改用 单据日期/单据编号/业务类型/生产车间/工序/设备（实用查询，全部走基础档案参照）；真实查询字段 部门/经手人/预开工日/预完工日 均保留在 dataSchema 表头。
2. **计划开工日/计划完工日 vs 预开工日/预完工日**：任务描述用词「计划开工日/计划完工日」，真实 T+ 列名为 预开工日（PreStartDate）/预完工日（PreFinishDate），本配置采用真实列名（§一.2）。
3. **工序编码非真实可见列**：dom.json A 区可见列只有 工序名称；工序编码为任务指定（对齐 MANU_ORDER/PROCESS_REPORT 工序行结构），参照 OP 带出 工序名称/生产车间；gridTabs 不含 工序编码（只放真实可见列）。
4. **工序加工要求（ProcessRequirement）/单位标准工时/本币委外单价/本币委外金额/班组成员/派工行码 等真实可见列省略**：20 字段已满（任务 16-20）；OP 档案备注如需带入 工序加工要求，可后续把该列加入明细字段（同时入 gridTabs 与种子行）。
5. **生产车间→DEPT**：任务指定规范化；DEPT 档案已含 D05 熔铸车间/D06 轧制车间/D07 精整车间/D08 测试车间。
6. **工人 赵敏**：任务指定工人集合 张伟/李娜/王强/赵敏；EMP 档案现有 E001-E008（张伟/李娜/王芳/陈强/王强/李丽/孙涛/赵刚）无 赵敏——种子按任务保留 赵敏，参照弹窗仍可用其余工人；如需严格对齐 EMP 可把 赵敏 改为 赵刚。
7. **工序编码 PX010-PX013**：OP 档案现有 PX001 下料/PX002 车削/PX003 铣削/PX005 热处理/PX007 检验；熔铸/挤压/时效/精整 为任务指定真实 T+ 工序，沿用 PX 系列新增编号（下料 复用 PX001）。
8. **派工加工状态/业务类型 选项**：T+ 无固定枚举抓取，按业务语义自定义（未派工/已派工/完工；工序派工/委外派工）。
9. **panelCode 命名**：MATERIAL_OUT.design.md 曾建议 `DISPATCH_ORDER`；按任务定 `panelCode='DISPATCH'`（与文件目录/面板码一致）。
10. **gridTabs ⊆ detail 验证**：明细 18 列、汇总 9 列均 ⊆ 20 个明细字段（node 脚本验证通过）。
11. **审批**：任务指定 单据状态 三态（草稿/已审核/已中止），审核组用 审核/弃审 直接过审；若后续要并入审批流，把 DISPATCH 加进 `APPROVAL_PANELS`（engine.js L11434）即可自动补「审批」组与「审批中」状态，无需改本配置。
12. 未实现按钮（引入常用单据/设置默认功能/生成材料出库单/中止（释放未执行量）/导出 等）保留占位，点击走 engine.UNIMPLEMENTED 提示（规范 §三.5），界面与 T+ 保持一致。
13. 界面风格：无卡通/表情/装饰（规范 §四/§14.4 定稿）。
