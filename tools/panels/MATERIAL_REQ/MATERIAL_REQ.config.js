// ==================== 领料申请单 ST1039（MATERIAL_REQ）面板配置 ====================
// 来源：docs/ref/tplus-live/mech-20260819/src/material_req-list.dom.json（真实 T+ h4t 抓取）
// 结构：metadata.panelPageDto.tablePages[0].queryFields/gridTabs、formPages、panelButtons、
//       buttonGroups、dataSchema.fields、detail.tabs[0].fields —— 与 PU_ORDER_CONFIG 同构，可直接拼入 engine.js。
// 依赖：today（engine.js 顶部 const today），参照面板 PARTNER/EMP/DEPT/PROJ/WH/INV（BASE_CONFIGS 已注册）。
// 编号前缀：LL-（领料），前缀登记由主会话在 PxService.generateFormNo 完成（本文件不写引擎）。
const MATERIAL_REQ_CONFIG = {
  metadata: {
    panelCode: 'MATERIAL_REQ',
    panelName: '领料申请单',
    panelCategory: '单据',
    autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '领料申请单列表',
        // 真实 T+ ST1039 表头查询字段（机构/单据日期/单据编号/业务类型/委外供应商/仓库/生产车间/部门/领料申请人/预计领料日期/来源单据/来源单号），取 6 个高频查询项
        queryFields: [
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['领料申请', '委外发料'] },
          { dataName: '委外供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false } },
          { dataName: '部门', dataType: '参照', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
          { dataName: '生产车间', dataType: '下拉框', options: ['熔铸车间', '轧制车间', '精整车间', '测试车间'] },
        ],
        gridTabs: [
          // 真实可见列前 16 个（加工单号…现存量），列序对齐抓取 cols[hidden:false]
          { label: '明细', rowSource: 'detail', columns: ['加工单号', '产品编码', '产品名称', '仓库', '材料名称', '计量单位', '数量', '中止数量', '智能选单', '倒冲料', '行中止', '累计领料数量', '累计调拨数量', '可用量', '可用量说明', '现存量'] },
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['产品编码', '产品名称', '材料名称', '计量单位', '数量', '累计领料数量', '可用量', '现存量'] },
        ],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '领料申请单',
        fieldNames: '单据日期,单据编号,业务类型,委外供应商,生产车间,部门,领料申请人,经手人,项目,仓库,预计领料日期,来源单据,来源单号',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
    ],
    // 对齐真实 T+ ST1039 工具栏（topText 含下拉全名）+ 页面开发规范 buttonGroups {name, actions}
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选单', '选生产加工单', '选生产加工单(新增材料)', '选委外加工单', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      { name: '审核', actions: ['审核', '弃审'] },
      { name: '生单', actions: ['生成材料出库单', '生成材料出库单(分单)', '生成材料出库单(退料)', '生成委外发料单', '生成委外发料单(分单)', '生成委外发料单(退料)', '生成调拨单', '生成调拨单(分单)', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '查找', actions: ['查找', '刷新'] },
      { name: '工具', actions: ['现存量查询', '变更历史', '联查', '生单流程联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出'] },
      { name: '导入', actions: ['下载导入模板', '导入'] },
      { name: '更多', actions: ['复制', '导出', '退出'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '领料申请', options: ['领料申请', '委外发料'] },
      { dataName: '委外供应商', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false }, refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '停用'] },
      { dataName: '生产车间', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['熔铸车间', '轧制车间', '精整车间', '测试车间'] },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '领料申请人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '原料仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '预计领料日期', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '来源单据', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [{ label: '数量合计', field: '数量' }, { label: '金额合计', field: '金额' }],
      // 计算链：金额 = 数量 × 单价；可用量 = 现存量 - 累计领料数量（领料后剩余可用）
      calc: [
        { target: '金额', formula: '数量 * 单价', round: 2 },
        { target: '可用量', formula: '现存量 - 累计领料数量', round: 2 },
      ],
      fields: [
        { dataName: '加工单号', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '产品编码', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { '停用': false }, refMap: [{ from: '存货名称', to: '产品名称' }], refColumns: ['存货编码', '存货名称', '规格型号', '所属类别', '品牌', '计量单位', '停用'] },
        { dataName: '产品名称', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '材料名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { '停用': false }, refColumns: ['存货编码', '存货名称', '规格型号', '所属类别', '品牌', '计量单位', '停用'] },
        { dataName: '仓库', dataType: '参照', isRequired: true, defaultValue: '原料仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: 'kg', options: ['件', 'kg', '套', '升'] },
        { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '单价', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '金额', dataType: '小数', computed: true },
        { dataName: '中止数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '智能选单', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '倒冲料', dataType: '是否', isRequired: false, defaultValue: false },
        { dataName: '行中止', dataType: '是否', isRequired: false, defaultValue: false },
        { dataName: '累计领料数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '累计调拨数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '可用量', dataType: '小数', computed: true },
        { dataName: '可用量说明', dataType: '文本', computed: true },
        { dataName: '现存量', dataType: '小数', computed: true },
      ],
    }],
  },
  // 拉式选单：选生产加工单 → 材料明细带入（对齐 T+ 选单前提：仅已审核且未中止的来源单）
  selectConfig: {
    source: 'MANU_ORDER',
    title: '选生产加工单',
    tip: '仅显示已审核且未中止的生产加工单，选中后材料明细带入领料申请单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '预完工日', '产品名称', '数量', '生产单位'],
    detailKey: 'materials',
    headerMap: [
      { from: '单据编号', to: '来源单号' },
      { from: '单据编号', to: '加工单号' },
    ],
    detailMap: [
      { from: '材料编码', to: '产品编码' },
      { from: '材料名称', to: '产品名称' },
      { from: '材料名称', to: '材料名称' },
      { from: '计量单位', to: '计量单位' },
      { from: '计划数量', to: '数量' },
      { from: '现存量', to: '现存量' },
    ],
  },
}

// ==================== 领料申请单种子数据（一张已审核供材料出库单选单，一张草稿） ====================
let MATERIAL_REQ_ROWS = [
  {
    '编号': 'LL-2026-08-0001', '单据日期': '2026-08-15', '单据编号': 'LL-2026-08-0001', '单据状态': '已审核',
    '业务类型': '领料申请', '委外供应商': '', '生产车间': '熔铸车间', '部门': '仓储部',
    '领料申请人': '张伟', '经手人': '张伟', '项目': '铝棒深加工', '仓库': '原料仓',
    '预计领料日期': '2026-08-16', '来源单据': '生产加工单', '来源单号': 'MO-2026-08-1501',
    '制单人': 'admin', '审核人': '系统管理员', '审核日期': '2026-08-15', '创建时间': '2026-08-15 09:00', '更新时间': '2026-08-15 09:10',
    detail: { items: [
      { '加工单号': 'MO-2026-08-1501', '产品编码': 'CP001', '产品名称': '铝型材-散热片', '材料名称': '铝棒 Φ80', '仓库': '原料仓', '计量单位': 'kg', '数量': 200, '单价': 15.5, '金额': 3100, '中止数量': 0, '智能选单': '', '倒冲料': false, '行中止': false, '累计领料数量': 120, '累计调拨数量': 0, '可用量': 680, '可用量说明': '充足', '现存量': 800 },
      { '加工单号': 'MO-2026-08-1501', '产品编码': 'CP001', '产品名称': '铝型材-散热片', '材料名称': '6061铝锭', '仓库': '原料仓', '计量单位': 'kg', '数量': 500, '单价': 12.8, '金额': 6400, '中止数量': 0, '智能选单': '', '倒冲料': false, '行中止': false, '累计领料数量': 300, '累计调拨数量': 0, '可用量': 5700, '可用量说明': '充足', '现存量': 6000 },
      { '加工单号': 'MO-2026-08-1502', '产品编码': 'CP003', '产品名称': '减速箱体 A', '材料名称': '轴套 C', '仓库': '半成品仓', '计量单位': '套', '数量': 50, '单价': 88, '金额': 4400, '中止数量': 0, '智能选单': '', '倒冲料': false, '行中止': false, '累计领料数量': 10, '累计调拨数量': 0, '可用量': 90, '可用量说明': '充足', '现存量': 100 },
    ] },
  },
  {
    '编号': 'LL-2026-08-0002', '单据日期': '2026-08-18', '单据编号': 'LL-2026-08-0002', '单据状态': '草稿',
    '业务类型': '领料申请', '委外供应商': '华东热处理厂', '生产车间': '精整车间', '部门': '精整车间',
    '领料申请人': '李娜', '经手人': '李娜', '项目': '散热片批量', '仓库': '辅料仓',
    '预计领料日期': '2026-08-20', '来源单据': '', '来源单号': '',
    '制单人': 'admin', '创建时间': '2026-08-18 14:00', '更新时间': '2026-08-18 14:00',
    detail: { items: [
      { '加工单号': '', '产品编码': 'CP002', '产品名称': '铝板 6061', '材料名称': '铝板 6061', '仓库': '辅料仓', '计量单位': '件', '数量': 100, '单价': 0, '金额': 0, '中止数量': 0, '智能选单': '', '倒冲料': false, '行中止': false, '累计领料数量': 0, '累计调拨数量': 0, '可用量': 1500, '可用量说明': '充足', '现存量': 1500 },
    ] },
  },
]