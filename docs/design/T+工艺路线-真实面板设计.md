# T+ 工艺路线 - 真实面板设计（2026-08-19 抓取实测）

| 属性 | 内容 |
|---|---|
| 文档类型 | 场景设计 |
| 适用场景 | 生产制造 |
| 维护状态 | 调研基线 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../README.md) |

> 来源：h2t.chanjet.com 演示账套 [012007] 轻MES（账号 tplusdemo128xx，自动分配）
> 菜单：基础设置 → 基础设置 → 基本信息 → 工艺路线（代码 AA1055，DoubleList 双表型档案，带审核/弃审）
> 抓取存档：`docs/ref/tplus-live/route-AA1055/`（route-aa1055.dom.json 列表结构 / route-aa1055-probe.json 双表+点击联动 / route-aa1055-probe2.json 选中行明细 / route-aa1055-form.json / route-aa1055.png 截图）
> 工具：tools/tplus-grab/grab.cjs（基础抓取）+ probe-doublelist.cjs（双表深度探针 + 真实鼠标点行联动）+ probe-action.cjs（工具栏按钮点击跟随）

## 一、页面形态

- **URL**：`https://h2t.chanjet.com/tplus/BAPView/DoubleList.aspx?mId=aa1055&pId=doubleListView&&sysId=aa`
- **布局**：左右双表（DoubleList）
  - 左侧：工艺路线主表（头 + 数据行 7 行 + 分页条「每页记录数 / 跳转到页 / 当前第1页,共1页,总共7条记录」）
  - 右侧：选中路线的工序明细（14 列）
- **交互**：单击左侧主表行 → 右侧明细联动刷新（真实鼠标点击验证：选中 002/制门 → 明细 4 行）
- **新增/修改**：就地编辑（工具栏「新增」→ ajaxpro `RecordAction&args.action=Add` 返回 `{"value":null}`，页面原地进入编辑态，主表行内输入；不跳转、不弹窗）

## 二、工具栏（实测 17 项，含下拉）

```
新增 | 修改 | 删除 | 审核 | 弃审 | 查找 | 栏目 | 打印(打印/预览 Alt+/) | 导入(下载工艺路线模板/导入工艺路线) | 导出 | 刷新 | 退出
```

- 分组形态与单据页一致（tb-text 按钮 + 下拉）；「打印」「导入」为下拉组
- 与单据面板差异：无 保存/选单/生单/变更/设置/更多 组——档案就地编辑模式

## 三、主表列定义（左侧，9 列）

| 可见 | 列头 | 服务端字段 a | 类型 | 宽度 | 备注 |
|---|---|---|---|---|---|
| 隐藏 | 工艺路线ID | ID | Int32 | 200px | 主键 |
| ✅ | 工艺路线编码 | Code | String | 200px | 必填（新增就地录入） |
| ✅ | 工艺路线名称 | Name | String | 200px | 必填 |
| ✅ | 单据状态 | VoucherState_Name | String | 90px | 生效 / 未审（=T+ 审核状态） |
| ✅ | 停用 | Disabled | Byte | 90px | 0/1（否/是） |
| ✅ | 制单人 | Maker | String | 90px | |
| ✅ | 审核人 | Auditor | String | 90px | 未审时为空 |
| ✅ | 审核日期 | AuditedDate | DateTime | 90px | 未审时为空 |
| 隐藏 | 时间戳 | Ts | String | 90px | 并发控制 |

分页：每页 7 条（演示账套 PerPageSize=7），列表查询接口 `ExecuteAjaxAction&args.action=PageIndex` 返回 DataTable。

## 四、明细列定义（右侧，14 列）

| 列头 | 服务端字段 a | 类型 | 宽度 | 对应轻MES参照 |
|---|---|---|---|---|
| 加工顺序 | JobSequence | Int | 90px | —（行号） |
| 工序编码 | Process_Code | String | 100px | → OP 工序参照 |
| 工序名称 | Process_Name | String | 100px | OP 带出 |
| 加工方式 | ProcessMode_Name | String | 90px | 自制 / 委外（下拉） |
| 生产车间 | WorkShop_Name | String | 100px | → 车间（WORKSHOP 无档案则下拉） |
| 工资类型 | RoutingDetailDTO_Process_SalaryType_Name | String | 90px | 计件工资/计时工资（下拉） |
| 计件依据 | RoutingDetailDTO_Process_PieceworkBasis_Name | String | 90px | 合格数量/汇报数量（下拉） |
| 委外供应商 | Partner_Name | String | 100px | → PARTNER（仅委外行填） |
| 按辅单位计价 | RoutingDetailDTO_isUseProcessSubunit | Bool | 90px | 是否 |
| 辅单位 | RoutingDetailDTO_Process_ProcessSubUnit_Name | String | 90px | → UOM |
| 换算率 | RoutingDetailDTO_ProcessSubUnitChangeRate | Decimal | 90px | 小数 |
| 默认报工数量 | RoutingDetailDTO_DefaultReportQuantity | Decimal | 90px | 小数 |
| 关键工序 | RoutingDetailDTO_KeyProcess | Bool | 60px | 是否 |
| 标准合格率% | RoutingDetailDTO_StandardQualificationrate | Decimal | 90px | 小数（Percent 列，100.00%） |

## 五、真实数据样例（7 条主表）

| ID | 编码 | 名称 | 状态 | 停用 | 制单人 | 审核人 | 审核日期 |
|---|---|---|---|---|---|---|---|
| 37 | 002 | 制门 | 生效 | 否 | tplusdemo12851 | tplusdemo12851 | 2026-03-23 |
| 39 | 0023 | 2323 | 生效 | 否 | tplusdemo12855 | tplusdemo12852 | 2026-07-21 |
| 42 | 0803 | 测试 | 未审 | 否 | tplusdemo12860 | — | — |
| 40 | 1 | 壹号线 | 生效 | 否 | tplusdemo12852 | tplusdemo12852 | 2026-05-26 |
| 12 | 1002 | 测试 | 生效 | 否 | 10000000101 | tplusdemo12857 | 2026-08-02 |
| 41 | 666 | 钣金 | 生效 | 否 | tplusdemo12860 | tplusdemo12862 | 2026-06-11 |
| 38 | JG01 | 精工加工 | 生效 | 否 | tplusdemo12864 | tplusdemo12857 | 2026-08-02 |

明细样例（002 制门，4 行）：

| 加工顺序 | 工序编码 | 工序名称 | 加工方式 | 生产车间 | 工资类型 | 计件依据 | 委外供应商 | 按辅单位计价 | 辅单位 | 换算率 | 默认报工数量 | 关键工序 | 标准合格率% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1001 | 工序1 | 自制 | 半成品车间 | 计件工资 | 合格数量 | — | 否 | — | — | — | 是 | 100.00% |
| 2 | 1001 | 工序1 | 自制 | — | 计件工资 | 合格数量 | — | 否 | — | — | — | 是 | 100.00% |
| 3 | 1001 | 工序1 | 自制 | — | 计件工资 | 合格数量 | — | 否 | — | — | — | 是 | 100.00% |
| 4 | 1003 | 工序3 | 委外 | — | 计件工资 | 合格数量 | — | 否 | — | — | — | 是 | 100.00% |

## 六、服务端契约（ajaxpro 方法，抓包确认）

- 控制器：`Ufida.T.AA.UIP.RoutingDoubleListController`（ajaxpro）
- `ExecuteAjaxAction&args.action=PageIndex`：分页查询主表 → DataTable（字段类型：ID Int32 / Code String / Name String / VoucherState_Name String / Disabled Byte / Maker String / Auditor String / AuditedDate DateTime / Ts String）
- `RecordAction&args.action=Add`：进入新增（返回 null）
- `GetChildListDTOs(id)`：取选中路线明细（id=主表 ID）
- 其他：GetDynamicValues / GetNewPageEnderInfo / GetAuditBtnIsEnable / GridColumnResized / GridColumnSwapped / GetTreeChildren / DeleteTreeNode / 打印系列
- 按钮事件绑定：工具栏按钮为 `A.tb-enable`（href=javascript:void(0)，原生 click 监听器，事件走 T+ 自定义委托）——**DOM .click() 与 CDP 坐标点击均有效**（新增已验证触发 ajaxpro 请求）；双击/行选中需真实鼠标事件（dispatchEvent 无效，必须 Input.dispatchMouseEvent）

## 七、轻MES ROUTE 面板映射建议

- **面板类型**：基础档案（panelCategory='基础档案'，参照现有 DEPT/EMP 等）或「单单据面板」——按数据量选择；本抓取建议沿用档案型（每行一条路线）
- **主表字段**（dataSchema.fields / gridTabs[0].columns 同步）：
  - 工艺路线编码（文本，必填，参照 ROUTE 自编号 或 手工录入）、工艺路线名称（文本，必填）
  - 单据状态（下拉：未审/生效，审核驱动）、停用（是否）、制单人（文本）、审核人（文本）、审核日期（日期）
- **明细字段**（detail.tabs[0].fields，key=routes 或 processDetails）：
  - 加工顺序（整数，自动序号）、工序编码（参照→OP，带出 工序名称/生产车间）、工序名称（OP 带出）、加工方式（下拉：自制/委外，委外行显示 委外供应商）、生产车间（下拉或 OP 带出）、工资类型（下拉：计件工资/计时工资）、计件依据（下拉：合格数量/汇报数量）、委外供应商（参照→PARTNER）、按辅单位计价（是否）、辅单位（参照→UOM）、换算率（小数）、默认报工数量（小数）、关键工序（是否）、标准合格率%（小数）
- **工具栏**：新增/修改/删除/审核/弃审/查找/栏目(未实现提示)/打印(打印/预览)/导入(下载工艺路线模板/导入工艺路线)/导出/刷新/退出
- **状态机**：草稿→审核(未审→生效)；弃审(生效→未审)——与 T+ 一致（T+ 状态显示 生效/未审，对应轻MES 已审核/草稿）
- **联动**：列表单击行 → 下方明细区展示该路线工序（复用 BOM 联动交互模式）
- **参照关系**（能对应必须对应）：工序编码→OP、委外供应商→PARTNER、辅单位→UOM；生产车间/工资类型/计件依据 无独立档案面板，保留下拉并在字段注释说明

## 八、后续待补（可选）

- 「新增」编辑态控件形态（就地编辑行内 input/select 结构）未抓取——交互为就地编辑，轻MES 已有 内联新增行 能力，控件形态按 T+ 惯例（编码/名称=输入框、状态=下拉、停用=是否）
- 若需像素级复刻：vision_html_screenshot 渲染 route-aa1055.html → vision_pixel_diff 对比 route-aa1055.png 迭代
