// ==================== 成品报检单 #QM10（FINISH_INSPECT）2026-08-19 真实 T+ 抓取一比一复刻 ====================
// 数据来源：docs/ref/tplus-live/mech-20260819/qc/finish_inspect-list.dom.json
//           （https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=QM&mId=QM10&pId=voucherView，真实菜单码 #QM10，
//             真实菜单树 qc-menu-tree.json：质检管理 → 报检单 → 成品报检单；rowCount=320 行真实数据）
// 结构对齐：tools/panels/PU_IN/PU_IN.config.js（完整产出范例）+ frontend/src/business/engine.js
//           PU_ORDER_CONFIG（metadata.panelPageDto.tablePages[0].queryFields/gridTabs + formPages + panelButtons
//             + buttonGroups + dataSchema.fields + detail.tabs[0].fields + selectConfig 模板）
//           MANU_ORDER_CONFIG（detail.tabs[0].key='products' 产成品明细，selectConfig.detailKey 参照）
//           FINISH_IN_CONFIG（产成品入库单：其 selectConfig 已用 MANU_ORDER 的 products，本面板同款拉式选单）
// 规范依据：docs/页面开发规范.md（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名、参照字段规范化）
//           + docs/design/面板交互设计规范.md（选单前提、单据编号标准、工具栏标准、删除组）
// 本文件为独立产出（不修改共享代码），可直接拼入 engine.js：置于 `const today`（L54）之后任意位置；
// 引用 today 变量（合并后由 engine.js 顶层声明提供；本文件用 __FINISH_INSPECT_TODAY 守卫兼容独立 require 验证）。
// 合并接入清单（panelOf/nextNoFor/flattenFor/menus.js/编号前缀 BJ-/init.sql）见 FINISH_INSPECT.notes.md。
// 角色：本面板为「选单源单据面板」——产成品入库单（FINISH_IN，选单 → 选成品报检单）与检验单（选单 → 选成品报检单）
//       的数据来源；自身工具栏「选单 → 选生产加工单」拉 MANU_ORDER（生产加工单 生单 → 生成成品报检单 的反向）。
//       选单弹窗按 {panelCode:'FINISH_INSPECT', condition:{单据状态:'已审核'}} 拉取，故种子含一张已审核单据。

// 兼容独立 require（node 验证脚本）：合并入 engine.js 后由顶层 `const today`（L54）提供，此处不重复声明
const __FINISH_INSPECT_TODAY = typeof today !== 'undefined' ? today : new Date().toISOString().slice(0, 10)

const FINISH_INSPECT_CONFIG = {
  metadata: {
    panelCode: 'FINISH_INSPECT',
    panelName: '成品报检单',
    panelCategory: '单据',
    autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '成品报检单列表',
        queryFields: [
          // 真实 T+ 查询区 7 项（*单据日期/*单据编号/*业务类型/供应商编码/*委外供应商/部门/负责人·业务员），
          // 按任务 3-6 个取核心 6 项：日期/编号/业务类型 + 报检类别/生产车间/仓库（真实 topText 线索：选生产加工单→生产车间）
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['完工报检', '退库报检'] },
          { dataName: '报检类别', dataType: '下拉框', options: ['自制加工报检', '委外加工报检'] },
          { dataName: '生产车间', dataType: '参照', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称' },
          { dataName: '仓库', dataType: '参照', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称' },
        ],
        gridTabs: [
          // 明细页签：真实可见列（dom.json cols hidden=false）中选 16 个，全部 ⊆ detail.tabs[0].fields（18 个），
          // 规范 §八.8.1：gridTabs 列名 = 明细字段名，列与明细字段对不上会空白
          { label: '明细', rowSource: 'detail', columns: ['产品名称', '存货编码', '规格型号', '计量单位', '报检数量', '合格数量', '不合格数量', '检验结果', '检验员', '检验方式', '抽检比例%', '检验要求', '班组', '工人', '仓库', '图号'] },
          // 汇总页签：按 产品名称 分组（groupKeyOf 命中），报检数量/合格数量/不合格数量 数值求和
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['仓库', '产品名称', '规格型号', '计量单位', '报检数量', '合格数量', '不合格数量', '检验结果'] },
        ],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '成品报检单',
        fieldNames: '单据日期,单据编号,业务类型,报检类别,生产车间,加工单号,仓库,经手人,项目,报检说明,备注',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏（2026-08-19 实测 #QM10 顶层按钮序列）：
    //   新增 | 选单 | 保存 | 修改 | 删除 | 审核 | 弃审 | 弃审 | 审批情况 | 生单 | 变更 | 生成批号 | 工具 | 联查 | 设置 | 打印 | 更多 | 更多
    // 下拉全名取自抓取 topText（选生产加工单/选委外加工单/选检验单、生成检验单/生成产成品入库单/生成委外入库单、
    //   联查 生产加工情况/委外加工情况/检验情况/产品入库情况/委外入库情况/装箱情况/生单流程联查）；
    // 按任务分组：新增/选单/保存/删除/审核/弃审/审批情况/生单/变更/生成批号/工具/联查/设置/打印/更多（+真实独立按钮 修改）；
    // 未实现动作保留（点击走 engine.UNIMPLEMENTED 提示「演示环境暂未实现，界面与 T+ 保持一致」）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选生产加工单', '选委外加工单', '选检验单', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印', '设置默认功能'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      { name: '审核', actions: ['审核'] },
      { name: '弃审', actions: ['弃审'] },
      { name: '审批情况', actions: ['审批情况'] },
      { name: '生单', actions: ['生成检验单', '生成产成品入库单', '生成委外入库单', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '生成批号', actions: ['生成批号'] },
      { name: '工具', actions: ['现存量查询', '变更历史'] },
      { name: '联查', actions: ['生产加工情况', '委外加工情况', '检验情况', '产品入库情况', '委外入库情况', '装箱情况', '生单流程联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出'] },
      { name: '更多', actions: ['复制', '导出', '退出', '放弃', '草稿', '附件', '刷新', '消息'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: __FINISH_INSPECT_TODAY },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      // 业务类型：报检单典型两分（对齐真实 T+ 机械行业：完工报检/退库报检；查询区同选项）
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '完工报检', options: ['完工报检', '退库报检'] },
      { dataName: '报检类别', dataType: '下拉框', isRequired: false, defaultValue: '自制加工报检', options: ['自制加工报检', '委外加工报检'] },
      // 参照字段全部走基础档案面板（规范 §2.1B + 开发约束十一-1）：生产车间→DEPT、仓库→WH、经手人→EMP、项目→PROJ
      { dataName: '生产车间', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      // 加工单号 = 来源生产加工单（选单 headerMap 目标；显示名 生产加工单号，dataName 对齐 selectConfig.to）
      { dataName: '加工单号', displayName: '生产加工单号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '报检说明', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [
        { label: '报检数量合计', field: '报检数量' },
        { label: '合格数量合计', field: '合格数量' },
        { label: '不合格数量合计', field: '不合格数量' },
      ],
      calc: [
        { target: '不合格数量', formula: '报检数量 - 合格数量', round: 2 },
      ],
      // 18 个明细字段：参照真实可见列（dom.json cols hidden=false 顺序选取）；
      // 产品名称→参照 INV（refMap 带出 编码/规格/计量单位/图号，对齐 FINISH_IN 产品名称）、检验员→参照 EMP、仓库→参照 WH；
      // 检验结果/检验方式/抽检比例% 为报检检验核心录入，班组/工人 对齐 MANU_ORDER 工序明细
      fields: [
        { dataName: '产品名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { '停用': false }, refMap: [{ from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '计量单位' }, { from: '图号', to: '图号' }], refColumns: ['存货编码', '存货名称', '规格型号', '所属类别', '品牌', '计量单位', '停用'] },
        { dataName: '存货编码', dataType: '文本' },
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升'] },
        { dataName: '报检数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '合格数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '不合格数量', dataType: '小数', computed: true },
        { dataName: '检验结果', dataType: '下拉框', isRequired: false, defaultValue: '待检', options: ['待检', '合格', '不合格', '让步接收'] },
        { dataName: '检验员', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
        { dataName: '检验方式', dataType: '下拉框', isRequired: true, defaultValue: '全检', options: ['全检', '抽检'] },
        { dataName: '抽检比例%', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '检验要求', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '班组', dataType: '下拉框', isRequired: false, defaultValue: '质检班', options: ['下料班', '车工班', '铣工班', '热处理班', '质检班'] },
        { dataName: '工人', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '图号', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '成品仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '预完工日', dataType: '日期', isRequired: false, defaultValue: '' },
        { dataName: '现存量', dataType: '小数', computed: true },
      ],
    }],
  },
  // 拉式选单：成品报检单选生产加工单（对齐 T+：选单 → 选生产加工单；仅已审核且未中止，交互规范 §3.3/§10 前提）
  // 同款配置参照 FINISH_IN_CONFIG.selectConfig（source:'MANU_ORDER'、detailKey:'products'）
  selectConfig: {
    source: 'MANU_ORDER',
    title: '选生产加工单',
    tip: '仅显示已审核且未中止的生产加工单，选中后产品明细带入成品报检单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '预完工日', '产品名称', '数量', '生产单位'],
    detailKey: 'products',
    headerMap: [
      { from: '单据编号', to: '加工单号' },
    ],
    detailMap: [
      { from: '产品名称', to: '产品名称' },
      { from: '规格型号', to: '规格型号' },
      { from: '生产单位', to: '计量单位' },
      { from: '数量', to: '报检数量' },
      { from: '现存量', to: '现存量' },
    ],
  },
}
// 成品报检单演示数据（2 张：①已审核 3 行明细，供 产成品入库单/检验单「选单 → 选成品报检单」作选单源；②草稿）
// 单据编号前缀 BJ-（对齐 面板交互设计规范.md §9 单据编号标准：前缀-yyyy-MM-dd+序号；
// 编号前缀登记由主会话在 PxService.generateFormNo 完成，本文件仅使用约定前缀）
// 数量一致性：不合格数量 = 报检数量 - 合格数量（对齐 calc 链）；存货与 INV 种子一致（CP001 铝棒 Φ80 等）
let FINISH_INSPECT_ROWS = [
  {
    '编号': 'BJ-2026-08-0001', '单据编号': 'BJ-2026-08-0001', '单据状态': '已审核', '审批状态': '已审批',
    '单据日期': '2026-08-18', '业务类型': '完工报检', '报检类别': '自制加工报检',
    '生产车间': '熔铸车间', '加工单号': 'MO-2026-08-0009', '仓库': '成品仓',
    '经手人': '张伟', '项目': '铝棒深加工', '报检说明': 'MO-2026-08-0009 完工报检', '备注': '首件已检，批量全检',
    '制单人': 'admin', '审核人': '系统管理员', '审核日期': '2026-08-18', '审核时间': '2026-08-18 15:20', '打印次数': 0,
    '创建时间': '2026-08-18 14:00', '更新时间': '2026-08-18 15:25', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '产品名称': '铝棒 Φ80', '存货编码': 'CP001', '规格型号': 'Φ80×3000', '计量单位': '件', '报检数量': 200, '合格数量': 198, '不合格数量': 2, '检验结果': '合格', '检验员': '赵刚', '检验方式': '全检', '抽检比例%': 100, '检验要求': '尺寸公差 ±0.05mm', '班组': '质检班', '工人': '赵刚', '图号': 'T-001', '仓库': '成品仓', '预完工日': '2026-08-20', '现存量': 800 },
      { '产品名称': '铝板 6061', '存货编码': 'CP002', '规格型号': '1500×3000×2', '计量单位': '件', '报检数量': 100, '合格数量': 100, '不合格数量': 0, '检验结果': '合格', '检验员': '赵刚', '检验方式': '全检', '抽检比例%': 100, '检验要求': '表面无划伤、无氧化斑', '班组': '质检班', '工人': '赵刚', '图号': 'T-002', '仓库': '成品仓', '预完工日': '2026-08-20', '现存量': 450 },
      { '产品名称': '减速箱体 A', '存货编码': 'CP004', '规格型号': 'JS-400', '计量单位': '件', '报检数量': 50, '合格数量': 48, '不合格数量': 2, '检验结果': '让步接收', '检验员': '孙涛', '检验方式': '全检', '抽检比例%': 100, '检验要求': '关键尺寸按图检测', '班组': '质检班', '工人': '孙涛', '图号': 'T-004', '仓库': '成品仓', '预完工日': '2026-08-22', '现存量': 120 },
    ] },
  },
  {
    '编号': 'BJ-2026-08-0002', '单据编号': 'BJ-2026-08-0002', '单据状态': '草稿',
    '单据日期': __FINISH_INSPECT_TODAY, '业务类型': '退库报检', '报检类别': '委外加工报检',
    '生产车间': '精整车间', '加工单号': '', '仓库': '成品仓',
    '经手人': '李娜', '项目': '散热片批量', '报检说明': '', '备注': '',
    '制单人': 'admin', '审核人': '', '审核日期': '', '审核时间': '', '打印次数': 0,
    '创建时间': __FINISH_INSPECT_TODAY + ' 10:00', '更新时间': __FINISH_INSPECT_TODAY + ' 10:00', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '产品名称': '铝型材-散热片', '存货编码': 'CP003', '规格型号': 'XD-6063-T5', '计量单位': '件', '报检数量': 300, '合格数量': 0, '不合格数量': 0, '检验结果': '待检', '检验员': '', '检验方式': '抽检', '抽检比例%': 20, '检验要求': '外观抽检 + 硬度抽检', '班组': '质检班', '工人': '', '图号': 'T-003', '仓库': '成品仓', '预完工日': '2026-08-28', '现存量': 600 },
    ] },
  },
]

// 供验证脚本 require 使用（node --check 仅查语法；require 本文件后可按 notes.md §五 做 gridTabs ⊆ detail.fields 断言）
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { FINISH_INSPECT_CONFIG, FINISH_INSPECT_ROWS }
}