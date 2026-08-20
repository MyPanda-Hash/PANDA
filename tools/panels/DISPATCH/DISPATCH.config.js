// ==================== ⑨ 工序派工单 #SW10（DISPATCH）2026-08-19 真实 T+ 抓取一比一复刻 ====================
// 数据来源：docs/ref/tplus-live/mech-20260819/qc/dispatch-list.dom.json
//           （https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=SW&mId=SW10&pId=voucherView，真实菜单码 #SW10；
//             T+ h4t 账套 [641164]户外用品 工序派工单列表，rowCount=465）
// 结构对齐：frontend/src/business/engine.js 的 PU_ORDER_CONFIG（L2041，单据面板模板）+ MANU_ORDER_CONFIG（L59，
//           选单来源：生产加工单的 工序明细）+ PROCESS_REPORT_CONFIG（L2849，同属工序级单据：工序编码/工序名称/
//           生产车间/设备/工人 字段参照）
//           （metadata.panelPageDto.tablePages[0].queryFields/gridTabs + formPages + panelButtons
//             + buttonGroups + dataSchema.fields + detail.tabs[0].fields + selectConfig）
// 规范依据：docs/页面开发规范.md（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名）
//           + docs/design/面板交互设计规范.md（选单前提、单据编号标准 §9、工具栏标准、删除组、Excel 导入）
// 本文件为独立产出（不修改共享代码），可直接拼入 engine.js：置于 `const today`（第 54 行）之后任意位置，
// 例如 PROCESS_REPORT_CONFIG 附近；引用 today 变量（合并后由 engine.js 顶层声明提供），
// 本文件用下方 TODAY 兼容独立运行（合并后沿用 engine.js 顶层 today，不重复声明）。
// 拼入 engine.js 时删除文件末尾 `module.exports` 行（仅用于本文件独立验证）。
// 合并接入清单（panelOf L12128 / nextNoFor L12297 前缀 PG- / flattenFor L12314 / VOUCHER_CODES L12424 /
//   menus.js / init.sql panel_config）见 DISPATCH.notes.md。
// 角色：本面板为「选单源单据面板」——工序级派工，来源于生产加工单（MANU_ORDER）的工序明细：
//       工具栏「选单 → 选生产加工单-工序」拉式选单（选单弹窗按 {panelCode:'MANU_ORDER',
//       condition:{单据状态:'已审核'}} 拉取，故 selectConfig.source = 'MANU_ORDER'）。
//       真实工具栏含「中止/取消中止」组（本配置 buttonGroups 已体现）。

// 兼容 today：合并入 engine.js 后取顶层 const today；独立运行（node 验证）时自取当前日期
const TODAY = (typeof today !== 'undefined') ? today : new Date().toISOString().slice(0, 10)

const DISPATCH_CONFIG = {
  metadata: {
    panelCode: 'DISPATCH',
    panelName: '工序派工单',
    panelCategory: '单据',
    autoCodeField: '单据编号', // 自动编码字段（前缀 PG-，登记见 DISPATCH.notes.md §四）
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '工序派工单列表',
        queryFields: [
          // 任务指定 6 项：单据日期/单据编号/业务类型/生产车间/工序/设备
          // （真实 T+ 查询区为 单据日期/单据编号/部门/经手人/预开工日/预完工日，见 notes §一.1/§五.1；
          //   本组为实用查询：车间/工序/设备 均走基础档案参照）
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['工序派工', '委外派工'] },
          { dataName: '生产车间', dataType: '参照', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
          { dataName: '工序', dataType: '参照', refPanel: 'OP', refField: '工序名称', displayField: '工序名称', filter: { '是否停用': false } },
          { dataName: '设备', dataType: '参照', refPanel: 'EQUIP', refField: '设备名称', displayField: '设备名称', filter: { '停用': false } },
        ],
        gridTabs: [
          // 明细页签：真实可见列（dom.json cols hidden=false 顺序截取）中选 18 个，全部 ⊆ detail.tabs[0].fields，
          // 规范 §八.8.1：gridTabs 列名 = 明细字段名，列与明细字段对不上会空白
          { label: '明细', rowSource: 'detail', columns: ['产品名称', '工序名称', '生产车间', '工作中心', '设备', '班组', '工人', '加工类型', '计划数量', '已派工数量', '派工数量', '计量单位', '预开工日', '预完工日', '派工加工状态', '累计汇报数量', '规格型号', '备注'] },
          // 汇总页签：按 工序名称 分组（groupKeyOf 命中），计划数量/已派工数量/派工数量/累计汇报数量 数值求和
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['工序名称', '生产车间', '工作中心', '班组', '计量单位', '计划数量', '已派工数量', '派工数量', '累计汇报数量'] },
        ],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '工序派工单',
        fieldNames: '单据日期,单据编号,业务类型,生产车间,加工单号,产品名称,预开工日,预完工日,经手人,项目,部门,备注',
        bottomOperationBarBtn: [
          { buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' },
          { buttonName: '弃审' }, { buttonName: '中止' }, { buttonName: '取消中止' }, { buttonName: '放弃' },
        ],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' },
      { buttonName: '中止' }, { buttonName: '取消中止' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏（2026-08-19 实测 #SW10 顶层按钮序列）：
    //   新增 | 选单 | 保存 | 修改 | 删除 | 弃审 | 弃审 | 审核 | 审批情况 | 生单 | 变更 | 中止 | 取消中止
    //   | 工具 | 联查 | 设置 | 打印 | 更多
    // 下拉全名取自抓取 topText（选单=选生产加工单-工序/-材料/-增加联副产品；生单=生成材料出库单…/生成工序汇报单…；
    //   中止=中止/中止（释放未执行量））；未实现动作保留（点击走 engine.UNIMPLEMENTED 提示「演示环境暂未实现」）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选生产加工单-工序', '选生产加工单-材料', '选生产加工单-增加联副产品', '设置默认功能'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存打印', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      { name: '审核', actions: ['审核', '弃审'] },
      { name: '审批情况', actions: ['审批情况'] },
      { name: '生单', actions: ['生成材料出库单', '生成材料出库单(分单)', '生成材料出库单（退料）', '生成工序汇报单（自制汇报）', '生成工序汇报单（委外汇报）', '生成工序汇报单（自制汇报退回）', '生成工序汇报单（委外汇报退回）', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '中止', actions: ['中止', '中止（释放未执行量）', '设置默认功能'] },
      { name: '取消中止', actions: ['取消中止'] },
      { name: '工具', actions: ['现存量查询', '变更历史', '生单流程联查'] },
      { name: '联查', actions: ['执行情况', '派工情况', '联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '工序流转卡打印', '导出'] },
      { name: '更多', actions: ['复制', '放弃', '草稿', '附件', '刷新', '消息'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      // 表头字段（工序派工单典型 12 项；预开工日/预完工日 采用真实 T+ 列名，任务描述中的 计划开工日/计划完工日 为其语义对应，见 notes §五.2）
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: TODAY },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '工序派工', options: ['工序派工', '委外派工'] },
      // 参照字段规范化：生产车间/部门 → DEPT（DEPT 档案含 D05 熔铸车间/D06 轧制车间/D07 精整车间/D08 测试车间）
      { dataName: '生产车间', dataType: '参照', isRequired: true, defaultValue: '熔铸车间', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '加工单号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '产品名称', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '预开工日', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '预完工日', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [
        { label: '计划数量合计', field: '计划数量' },
        { label: '已派工数量合计', field: '已派工数量' },
        { label: '派工数量合计', field: '派工数量' },
        { label: '累计汇报数量合计', field: '累计汇报数量' },
      ],
      // 20 个明细字段（工序级）：真实可见列为主（加工单号/产品名称/规格型号/加工类型/工序名称/已派工数量/派工数量/
      // 生产车间/工作中心/班组/工人/设备/委外供应商/预开工日/预完工日/派工加工状态/累计汇报数量/备注），
      // 工序编码 为任务指定（非真实可见列，参照 OP 带出 工序名称/生产车间/工序加工要求），
      // 计划数量/计量单位 从生产加工单工序明细带入（对齐 MANU_ORDER 工序行）
      fields: [
        // 参照字段：弹窗拉取 工序 面板数据勾选导入（带出 工序名称/默认车间→生产车间；
        // 工序加工要求（ProcessRequirement）为真实可见列，OP 档案备注如需带入可后续把该列加入明细字段，见 notes §五.4）
        { dataName: '工序编码', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'OP', refField: '工序编码', displayField: '工序名称', filter: { '是否停用': false }, refMap: [{ from: '工序名称', to: '工序名称' }, { from: '默认车间', to: '生产车间' }], refColumns: ['工序编码', '工序名称', '默认车间', '加工方式', '标准合格率%', '备注', '是否停用'] },
        { dataName: '工序名称', dataType: '文本' },
        { dataName: '产品名称', dataType: '文本' },
        { dataName: '生产车间', dataType: '参照', isRequired: false, defaultValue: '熔铸车间', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
        { dataName: '工作中心', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WC', refField: '工作中心名称', displayField: '工作中心名称', filter: { '停用': false } },
        { dataName: '设备', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EQUIP', refField: '设备名称', displayField: '设备名称', filter: { '停用': false } },
        { dataName: '班组', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'TEAM', refField: '班组名称', displayField: '班组名称', filter: { '是否停用': false } },
        { dataName: '工人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
        { dataName: '加工类型', dataType: '下拉框', isRequired: true, defaultValue: '自制', options: ['自制', '委外'] },
        { dataName: '计划数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '已派工数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '派工数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升'] },
        { dataName: '预开工日', dataType: '日期' },
        { dataName: '预完工日', dataType: '日期' },
        { dataName: '派工加工状态', dataType: '下拉框', isRequired: false, defaultValue: '未派工', options: ['未派工', '已派工', '完工'] },
        { dataName: '累计汇报数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '委外供应商', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false } },
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '备注', dataType: '文本' },
      ],
    }],
  },
  // 拉式选单（对齐 T+：工序派工单选生产加工单-工序，来源于生产加工单的工序明细）
  selectConfig: {
    source: 'MANU_ORDER',
    title: '选生产加工单',
    tip: '仅显示已审核且未中止的生产加工单，选中后工序明细带入工序派工单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '预完工日', '产品名称', '数量', '生产单位'],
    detailKey: 'items',
    headerMap: [
      { from: '单据编号', to: '加工单号' },
      { from: '预开工日', to: '预开工日' },
      { from: '预完工日', to: '预完工日' },
      { from: '生产车间', to: '生产车间' },
    ],
    detailMap: [
      { from: '产品名称', to: '产品名称' },
      { from: '规格型号', to: '规格型号' },
      { from: '工序编码', to: '工序编码' },
      { from: '工序名称', to: '工序名称' },
      { from: '生产车间', to: '生产车间' },
      { from: '工作中心', to: '工作中心' },
      { from: '设备', to: '设备' },
      { from: '班组', to: '班组' },
      { from: '工人', to: '工人' },
      { from: '加工类型', to: '加工类型' },
      { from: '计划数量', to: '计划数量' },
      { from: '工序单位', to: '计量单位' },
      { from: '委外供应商', to: '委外供应商' },
    ],
    // 选单带出工序明细行（对齐 PROCESS_REPORT detailRows：工序行本身不含产品信息，从单据头/产成品明细行补齐；
    // 合并入 engine.js 后引用其顶层 let MOCK_ROWS，独立运行兜底空数组）
    detailRows: (row) => {
      const all = (typeof MOCK_ROWS !== 'undefined') ? MOCK_ROWS : []
      const r = all.find((x) => x['编号'] === (row['编号'] || row['单据编号']))
      const prods = (r && r.detail && r.detail.products) || []
      const head = prods[0] || {}
      return ((r && r.detail && r.detail.processes) || []).map((p) => ({
        加工单号: r['锭号'] || r['单据编号'] || '',
        产品名称: p['产品名称'] ?? head['产品名称'] ?? '',
        规格型号: p['规格型号'] ?? head['规格型号'] ?? '',
        ...p,
      }))
    },
  },
}

// 工序派工单演示数据（2 张：①已审核 3 行工序明细，供 材料出库/工序汇报 等下游单据作选单源；②草稿）
// 单据编号前缀 PG-（对齐 面板交互设计规范.md §9 单据编号标准：前缀-yyyy-MM-dd+序号；
// 编号前缀登记由主会话在 PxService.generateFormNo 完成，本文件仅使用约定前缀 PG-）
// 工序数据与真实 T+ 工序一致（下料/熔铸/挤压/时效/精整，车间 熔铸车间/轧制车间/精整车间，
// 工人 张伟/李娜/王强/赵敏）；加工单号/产品/客户 与 engine.js MANU_ORDER/SO_ORDER 演示数据联动
// 数值自洽：已派工数量 ≤ 计划数量、派工数量 ≤ 计划数量；已派工行 派工加工状态=已派工
let DISPATCH_ROWS = [
  {
    '编号': 'PG-2026-08-0001', '单据编号': 'PG-2026-08-0001', '单据状态': '已审核', '审批状态': '已审批',
    '单据日期': '2026-08-14', '业务类型': '工序派工', '生产车间': '熔铸车间',
    '加工单号': 'MO-2026-08-0001', '产品名称': '铝棒 Φ80', '预开工日': '2026-08-14', '预完工日': '2026-08-20',
    '经手人': '张伟', '项目': '铝棒深加工', '部门': '熔铸车间', '备注': 'MO-2026-08-0001 首道派工（下料/熔铸/挤压）',
    '制单人': 'admin', '审核人': '系统管理员', '审核日期': '2026-08-14', '审核时间': '2026-08-14 09:30', '打印次数': 0,
    '创建时间': '2026-08-14 09:00', '更新时间': '2026-08-14 09:35', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '工序编码': 'PX001', '工序名称': '下料', '产品名称': '铝棒 Φ80', '生产车间': '熔铸车间', '工作中心': 'WC-01 熔铸中心', '设备': '锯床-01', '班组': '下料班', '工人': '王强', '加工类型': '自制', '计划数量': 300, '已派工数量': 300, '派工数量': 300, '计量单位': '件', '预开工日': '2026-08-14', '预完工日': '2026-08-16', '派工加工状态': '已派工', '累计汇报数量': 120, '委外供应商': '', '规格型号': 'Φ80×3000', '备注': '' },
      { '工序编码': 'PX010', '工序名称': '熔铸', '产品名称': '铝棒 Φ80', '生产车间': '熔铸车间', '工作中心': 'WC-01 熔铸中心', '设备': '熔炼炉-01', '班组': '熔铸班', '工人': '张伟', '加工类型': '自制', '计划数量': 300, '已派工数量': 200, '派工数量': 200, '计量单位': '件', '预开工日': '2026-08-16', '预完工日': '2026-08-18', '派工加工状态': '已派工', '累计汇报数量': 60, '委外供应商': '', '规格型号': 'Φ80×3000', '备注': '' },
      { '工序编码': 'PX011', '工序名称': '挤压', '产品名称': '铝棒 Φ80', '生产车间': '轧制车间', '工作中心': 'WC-02 轧制中心', '设备': '挤压机-01', '班组': '挤压班', '工人': '李娜', '加工类型': '自制', '计划数量': 300, '已派工数量': 150, '派工数量': 150, '计量单位': '件', '预开工日': '2026-08-18', '预完工日': '2026-08-20', '派工加工状态': '已派工', '累计汇报数量': 0, '委外供应商': '', '规格型号': 'Φ80×3000', '备注': '' },
    ] },
  },
  {
    '编号': 'PG-2026-08-0002', '单据编号': 'PG-2026-08-0002', '单据状态': '草稿',
    '单据日期': TODAY, '业务类型': '工序派工', '生产车间': '精整车间',
    '加工单号': 'MO-2026-08-0002', '产品名称': '铝型材-散热片', '预开工日': TODAY, '预完工日': '2026-08-25',
    '经手人': '李娜', '项目': '散热片批量', '部门': '精整车间', '备注': '',
    '制单人': 'admin', '审核人': '', '审核日期': '', '审核时间': '', '打印次数': 0,
    '创建时间': TODAY + ' 10:00', '更新时间': TODAY + ' 10:00', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '工序编码': 'PX012', '工序名称': '时效', '产品名称': '铝型材-散热片', '生产车间': '精整车间', '工作中心': 'WC-02 轧制中心', '设备': '时效炉-01', '班组': '时效班', '工人': '赵敏', '加工类型': '自制', '计划数量': 300, '已派工数量': 0, '派工数量': 0, '计量单位': '件', '预开工日': TODAY, '预完工日': '2026-08-22', '派工加工状态': '未派工', '累计汇报数量': 0, '委外供应商': '', '规格型号': 'XD-6063-T5', '备注': '' },
      { '工序编码': 'PX013', '工序名称': '精整', '产品名称': '铝型材-散热片', '生产车间': '精整车间', '工作中心': 'WC-03 机加中心', '设备': '精整线-01', '班组': '精整班', '工人': '赵敏', '加工类型': '自制', '计划数量': 300, '已派工数量': 0, '派工数量': 0, '计量单位': '件', '预开工日': TODAY, '预完工日': '2026-08-25', '派工加工状态': '未派工', '累计汇报数量': 0, '委外供应商': '', '规格型号': 'XD-6063-T5', '备注': '' },
    ] },
  },
]

// 供本文件独立验证（node 执行 / node --check）；拼入 engine.js 时删除本行
module.exports = { DISPATCH_CONFIG, DISPATCH_ROWS }
