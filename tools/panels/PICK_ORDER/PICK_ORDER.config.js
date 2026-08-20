// ==================== 配货单 PICK_ORDER（真实 T+ h4t DI20）2026-08-19 一比一复刻 ====================
// 抓取源：docs/ref/tplus-live/mech-20260819/src/pick_order-list.dom.json（h4t.chanjet.com DI20 配货单）
// 结构对齐 PU_ORDER_CONFIG：metadata.panelPageDto.tablePages[0].queryFields/gridTabs、formPages、
//   panelButtons、buttonGroups、dataSchema.fields、detail.tabs[0].fields（key=items，行 detail.items 与之对应）
// 编号前缀 PH- 为拟用前缀；正式前缀登记由主会话在 PxService.generateFormNo 完成（autoCodeField=单据编号）
// 依赖 engine.js 全局变量 today（本文件可直接拼入 engine.js；panelOf 注册见 PICK_ORDER.notes.md）
const PICK_ORDER_CONFIG = {
  metadata: {
    panelCode: 'PICK_ORDER',
    panelName: '配货单',
    panelCategory: '单据',
    autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '配货单列表',
        // 真实查询区 8 字段（*单据日期/*单据编号/*业务类型/出库仓库/入库仓库/预计交货日期/客户编码/客户），取 6 个高频字段
        queryFields: [
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['配货出库', '调拨出库'] },
          { dataName: '客户', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false } },
          { dataName: '项目', dataType: '参照', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
          { dataName: '出库仓库', dataType: '参照', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        ],
        gridTabs: [
          // 真实可见列前 16 个（cols hidden:false 顺序：入库仓库/客户/存货编码/存货名称/规格型号/计量单位/数量/零售价/零售金额/出库仓库/累计出库数量/累计调拨数量/累计入库数量/合理损耗数量/预计交货日期/可用量）
          { label: '明细', rowSource: 'items', columns: ['入库仓库', '客户', '存货编码', '存货名称', '规格型号', '计量单位', '数量', '零售价', '零售金额', '出库仓库', '累计出库数量', '累计调拨数量', '累计入库数量', '合理损耗数量', '预计交货日期', '可用量'] },
          { label: '汇总', rowSource: 'items', summary: true, columns: ['存货编码', '存货名称', '规格型号', '计量单位', '数量', '零售价', '零售金额'] },
        ],
        topBarBtn: [{ buttonName: '新增' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '配货单',
        fieldNames: '单据日期,单据编号,业务类型,客户,客户编码,出库仓库,入库仓库,经手人,项目,部门,预计交货日期,备注',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏分组（2026-08-19 实测 h4t DI20 topText：新增/选单[要货单,销售订单,销货单]/保存[保存,保存新增,保存为草稿,保存打印]/
    //   删除/审核/弃审/审批情况/生单[生成调拨单,生成销售出库单,生成其他出库单(调拨出库),生成其他出库单(配货出库)]/工具/联查/设置/打印/更多/智能导入）
    // 未实现动作保留（点击走 engine.UNIMPLEMENTED「演示环境暂未实现，界面与 T+ 保持一致」提示）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选单', '选要货单', '选销售订单', '选销货单', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存打印', '设置默认功能'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      { name: '审核', actions: ['审核', '弃审'] },
      { name: '生单', actions: ['生成调拨单', '生成销售出库单', '生成其他出库单(调拨出库)', '生成其他出库单(配货出库)', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '查找', actions: ['查找', '刷新'] },
      { name: '工具', actions: ['现存量查询', '拣货装箱(其他出库单)', '拣货装箱(调拨单)', '拣货装箱(销售出库单)', '联查', '要货情况', '销售订单情况', '销货情况', '其他出库情况', '调拨情况', '销售出库情况', '生单流程联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出'] },
      { name: '导入', actions: ['下载导入模板', '导入'] },
      { name: '更多', actions: ['复制', '导出', '退出'] },
    ],
    version: '1.0',
  },  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '配货出库', options: ['配货出库', '调拨出库'] },
      { dataName: '客户', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false }, refMap: [{ from: '往来单位编码', to: '客户编码' }], refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '停用'] },
      { dataName: '客户编码', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '出库仓库', dataType: '参照', isRequired: false, defaultValue: '成品仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '入库仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '预计交货日期', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [{ label: '数量合计', field: '数量' }, { label: '零售金额合计', field: '零售金额' }],
      // 计算链（对齐真实 DI20 明细：零售金额 = 数量 × 零售价；可用量 = 现存量 − 数量）
      calc: [
        { target: '零售金额', formula: '数量 * 零售价', round: 2 },
        { target: '可用量', formula: '现存量 - 数量', round: 2 },
      ],
      fields: [
        // 明细行客户（真实网格每行展示客户列；参照 PARTNER）
        { dataName: '客户', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false } },
        // 物料存货参照 INV（弹窗勾选带出 存货名称/规格型号/计量单位）
        { dataName: '存货编码', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { '停用': false }, refMap: [{ from: '存货名称', to: '存货名称' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '计量单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '计量单位', '停用'] },
        { dataName: '存货名称', dataType: '文本' },
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升'] },
        { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '零售价', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '零售金额', dataType: '小数', computed: true },
        // 仓库参照 WH（配货单 出库仓库/入库仓库 两列）
        { dataName: '出库仓库', dataType: '参照', isRequired: false, defaultValue: '成品仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '入库仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '累计出库数量', dataType: '小数', defaultValue: 0 },
        { dataName: '累计调拨数量', dataType: '小数', defaultValue: 0 },
        { dataName: '累计入库数量', dataType: '小数', defaultValue: 0 },
        { dataName: '合理损耗数量', dataType: '小数', defaultValue: 0 },
        { dataName: '预计交货日期', dataType: '日期' },
        { dataName: '可用量', dataType: '小数', computed: true },
        { dataName: '现存量', dataType: '小数', defaultValue: 0 },
        { dataName: '是否带票', dataType: '下拉框', defaultValue: '不带票', options: ['带票', '不带票'] },
      ],
    }],
  },
}// 配货单演示数据（表头 + items 配货明细行；对齐 INV/PARTNER/WH/DEPT/EMP 档案数据）
// 第 1 张已审核（3 行明细，供其它单据 选单/生单 拉取）；第 2 张草稿（2 行明细）
let PICK_ORDER_ROWS = [
  {
    "编号": "PH-2026-08-0001", "单据日期": "2026-08-15", "单据编号": "PH-2026-08-0001", "单据状态": "已审核",
    "业务类型": "配货出库", "客户": "华东铝业", "客户编码": "KH001", "出库仓库": "成品仓", "入库仓库": "",
    "经手人": "张伟", "项目": "铝材配货", "部门": "销售一部", "预计交货日期": "2026-08-20", "备注": "",
    "制单人": "admin", "审核人": "系统管理员", "审核日期": "2026-08-15", "创建时间": "2026-08-15 09:00", "更新时间": "2026-08-15 09:10",
    detail: { items: [
      { "客户": "华东铝业", "存货编码": "CP001", "存货名称": "铝棒 Φ80", "规格型号": "Φ80×3000", "计量单位": "件", "数量": 200, "零售价": 18.5, "零售金额": 3700, "出库仓库": "成品仓", "入库仓库": "", "累计出库数量": 200, "累计调拨数量": 0, "累计入库数量": 0, "合理损耗数量": 0, "预计交货日期": "2026-08-20", "可用量": 600, "现存量": 800, "是否带票": "带票" },
      { "客户": "华东铝业", "存货编码": "CP002", "存货名称": "铝板 6061", "规格型号": "1500×3000×2", "计量单位": "件", "数量": 100, "零售价": 25.5, "零售金额": 2550, "出库仓库": "成品仓", "入库仓库": "", "累计出库数量": 100, "累计调拨数量": 0, "累计入库数量": 0, "合理损耗数量": 0, "预计交货日期": "2026-08-20", "可用量": 350, "现存量": 450, "是否带票": "带票" },
      { "客户": "华东铝业", "存货编码": "CP003", "存货名称": "铝型材-散热片", "规格型号": "XD-6063-T5", "计量单位": "件", "数量": 300, "零售价": 22.6, "零售金额": 6780, "出库仓库": "成品仓", "入库仓库": "", "累计出库数量": 300, "累计调拨数量": 0, "累计入库数量": 0, "合理损耗数量": 0, "预计交货日期": "2026-08-22", "可用量": 300, "现存量": 600, "是否带票": "带票" },
    ] },
  },
  {
    "编号": "PH-2026-08-0002", "单据日期": today, "单据编号": "PH-2026-08-0002", "单据状态": "草稿",
    "业务类型": "调拨出库", "客户": "中天精工", "客户编码": "KH002", "出库仓库": "原料仓", "入库仓库": "原料仓",
    "经手人": "李娜", "项目": "", "部门": "销售一部", "预计交货日期": "2026-08-25", "备注": "",
    "制单人": "admin", "创建时间": today + " 14:00", "更新时间": today + " 14:00",
    detail: { items: [
      { "客户": "中天精工", "存货编码": "CP004", "存货名称": "减速箱体 A", "规格型号": "JS-400", "计量单位": "件", "数量": 50, "零售价": 260, "零售金额": 13000, "出库仓库": "原料仓", "入库仓库": "原料仓", "累计出库数量": 0, "累计调拨数量": 0, "累计入库数量": 0, "合理损耗数量": 0, "预计交货日期": "2026-08-25", "可用量": 70, "现存量": 120, "是否带票": "不带票" },
      { "客户": "中天精工", "存货编码": "CP005", "存货名称": "轴套 C", "规格型号": "ZT-C-30", "计量单位": "件", "数量": 800, "零售价": 6.8, "零售金额": 5440, "出库仓库": "原料仓", "入库仓库": "原料仓", "累计出库数量": 0, "累计调拨数量": 0, "累计入库数量": 0, "合理损耗数量": 0, "预计交货日期": "2026-08-25", "可用量": 800, "现存量": 1600, "是否带票": "不带票" },
    ] },
  },
]