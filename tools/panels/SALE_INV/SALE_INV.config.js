// ==================== 销货单 SALE_INV（T+ SA04）面板配置（独立设计产物，合并由主会话完成） ====================
// 真实来源：机械行业账套 h4t.chanjet.com 销货单列表页
//   docs/ref/tplus-live/mech-20260819/src/sale_inv-list.dom.json
//   （url=BAPView/Voucher.aspx?sysId=SA&mId=SA04；cols 可见列 / query 查询字段 / toolbar+topText 工具栏下拉全名）
// 配置模板：frontend/src/business/engine.js 的 PU_ORDER_CONFIG / invPanel 工厂结构（metadata.panelPageDto.tablePages[0].queryFields/gridTabs、formPages、panelButtons、buttonGroups、dataSchema.fields、detail.tabs[0].fields）
// 规范：docs/页面开发规范.md（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名）、docs/design/面板交互设计规范.md（选单/编号/审核组/删除组双动作）
// 说明：本文件可整体拼入 engine.js（依赖 engine.js 顶层 const today；勿重复声明）。编号前缀 XS-（销货单），前缀登记（PxService.generateFormNo）由主会话完成。
const SALE_INV_CONFIG = {
  metadata: {
    panelCode: 'SALE_INV',
    panelName: '销货单',
    panelCategory: '单据',
    autoCodeField: '单据编号', // 自动编码：XS-yyyy-MM-dd+2位日序（交互规范 §9）
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '销货单列表',
        // 表头查询字段：真实 query 为 单据日期*/单据编号*/业务类型*/发票号码，补 客户/项目 参照（规范 §2 表头字段区）
        queryFields: [
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['普通销售', '销售退货'] },
          { dataName: '客户', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false } },
          { dataName: '项目', dataType: '参照', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
          { dataName: '发票号码', dataType: '文本' },
        ],
        // A 区明细：真实可见列前 16 个（dom.json cols hidden=false 顺序：销售单位结存成本/税率%/仓库/项目/货物编码/存货名称/数量/销售单位/批号/现存量/折扣%/单价/含税单价/金额/税额/含税金额）
        // 货物编码 对应 INV 面板 存货编码（参照 INV 带出）；列名=字段名，字段类型由 fieldDefOf 反查 detail.tabs
        gridTabs: [
          { label: '明细', rowSource: 'detail', columns: ['销售单位结存成本', '税率%', '仓库', '项目', '存货编码', '存货名称', '数量', '销售单位', '批号', '现存量', '折扣%', '单价', '含税单价', '金额', '税额', '含税金额'] },
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['仓库', '存货名称', '规格型号', '销售单位', '数量', '单价', '金额', '税额', '含税金额'] },
        ],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '销货单',
        fieldNames: '单据日期,单据编号,业务类型,客户,客户编码,结算客户,部门,经手人,项目,仓库,结算方式,发票号码,备注',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增' }, { buttonName: '选单' }, { buttonName: '修改' }, { buttonName: '保存' }, { buttonName: '删除' },
      { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '生单' }, { buttonName: '变更' }, { buttonName: '查找' },
      { buttonName: '刷新' }, { buttonName: '工具' }, { buttonName: '设置' }, { buttonName: '打印' }, { buttonName: '导入' },
    ],
    // T+ 工具栏分组（dom.json toolbar/topText 下拉全名，2026-08-19 实测 SA04）
    // 未实现动作（引入常用单据/设置默认功能/生成销售发票/生成配货单/生成凭证/查找/刷新/导出/退出…）保留占位，点击走 engine.UNIMPLEMENTED 提示（规范 §三.5）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选返货单', '选销售订单', '选销售出库单', '选销货单(普通销售)', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印'] },
      { name: '删除', actions: ['删除', '删除单据'] }, // 规范 §12.1 删除组双动作
      { name: '审核', actions: ['审核', '弃审'] },
      { name: '生单', actions: ['生成销售发票(普通销售)', '生成销售发票(销售退货)', '生成销售出库单(普通销售)', '生成销售出库单(销售退货)', '生成销货单(销售退货)', '生成配货单', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '查找', actions: ['查找', '刷新'] },
      { name: '工具', actions: ['现存量查询', '变更历史', '联查', '生成凭证', '生单流程联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出'] },
      { name: '导入', actions: ['下载导入模板', '导入'] }, // 规范 §18 Excel 导入
      { name: '更多', actions: ['复制', '导出', '退出'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    // 表头字段：单据日期*/单据编号*/业务类型* 必填 + 真实表头字段；客户/结算客户→PARTNER、经手人→EMP、项目→PROJ、仓库→WH、部门→DEPT 参照化
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '普通销售', options: ['普通销售', '销售退货'] },
      { dataName: '客户', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false }, refMap: [{ from: '往来单位编码', to: '客户编码' }, { from: '往来单位名称', to: '结算客户' }], refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '结算客户', '分管部门', '停用'] },
      { dataName: '客户编码', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位编码', displayField: '往来单位名称', filter: { '停用': false } },
      { dataName: '结算客户', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false } },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '成品仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '结算方式', dataType: '下拉框', isRequired: false, defaultValue: '现结', options: ['现结', '赊销', '月结'] },
      { dataName: '发票号码', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [
        { label: '数量合计', field: '数量' },
        { label: '金额合计', field: '金额' },
        { label: '含税金额合计', field: '含税金额' },
      ],
      // 计算链：含税单价/金额/税额/含税金额/折扣金额（对齐真实 T+ 价税分离，参照 SO/SALE_OUT calc）
      calc: [
        { target: '含税单价', formula: '单价 * (1 + 税率% / 100)', round: 2 },
        { target: '金额', formula: '数量 * 单价', round: 2 },
        { target: '税额', formula: '数量 * (含税单价 - 单价)', round: 2 },
        { target: '含税金额', formula: '金额 + 税额', round: 2 },
        { target: '折扣金额', formula: '金额 * 折扣% / 100', round: 2 },
      ],
      // 明细字段：真实可见列（货物编码→存货编码参照 INV、存货名称参照 INV、仓库参照 WH、项目参照 PROJ、销售单位/批号/现存量/折扣%/单价/含税单价/金额/税额/含税金额/销售单位结存成本）+ 规格型号/折扣金额
      fields: [
        { dataName: '存货编码', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { '停用': false } },
        { dataName: '存货名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { '停用': false }, refMap: [{ from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '销售单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '计量单位', '停用'] },
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '仓库', dataType: '参照', isRequired: true, defaultValue: '成品仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
        { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '销售单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升'] },
        { dataName: '批号', dataType: '文本' },
        { dataName: '现存量', dataType: '小数', computed: true },
        { dataName: '销售单位结存成本', dataType: '小数', defaultValue: 0 },
        { dataName: '税率%', dataType: '小数', defaultValue: 13, options: [0, 3, 6, 9, 13] },
        { dataName: '单价', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '含税单价', dataType: '小数', computed: true },
        { dataName: '金额', dataType: '小数', computed: true },
        { dataName: '税额', dataType: '小数', computed: true },
        { dataName: '含税金额', dataType: '小数', computed: true },
        { dataName: '折扣%', dataType: '小数', defaultValue: 0 },
        { dataName: '折扣金额', dataType: '小数', computed: true },
      ],
    }],
  },
  // 选单（拉式）：选销售订单 → 表头/明细带入（交互规范 §3.3/§10；来源单必须已审核未中止，引擎查询固定条件 单据状态=已审核）
  // 本面板同时是其他面板的选单源：SALE_OUT「选销货单」、销售出库单/采购入库单「转成销货单」——种子含已审核单据即为此提供
  selectConfig: {
    source: 'SO_ORDER',
    title: '选销售订单',
    tip: '仅显示已审核且未中止的销售订单，选中后明细带入销货单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '业务员', '预计交货日期', '存货名称', '数量', '销售单位'],
    detailKey: 'items',
    headerMap: [
      { from: '客户', to: '客户' },
      { from: '结算客户', to: '结算客户' },
    ],
    detailMap: [
      { from: '存货名称', to: '存货名称' },
      { from: '存货编码', to: '存货编码' },
      { from: '规格型号', to: '规格型号' },
      { from: '销售单位', to: '销售单位' },
      { from: '数量', to: '数量' },
      { from: '现存量', to: '现存量' },
    ],
  },
}
// 种子数据：2 张（1 张已审核 3 行明细——供 SALE_OUT 等面板「选销货单」；1 张草稿）。字段与 dataSchema/detail.tabs[0].fields 一致。
let SALE_INV_ROWS = [
  {
    '编号': 'XS-2026-08-0001', '单据日期': '2026-08-16', '单据编号': 'XS-2026-08-0001', '单据状态': '已审核',
    '业务类型': '普通销售', '客户': '华东铝业', '客户编码': 'KH001', '结算客户': '华东铝业', '部门': '销售一部', '经手人': '张伟',
    '项目': '铝棒深加工', '仓库': '成品仓', '结算方式': '现结', '发票号码': 'FP20260816001', '备注': '华东铝业 8 月批量发货',
    '制单人': 'admin', '审核人': '系统管理员', '审核日期': '2026-08-16', '创建时间': '2026-08-16 09:00', '更新时间': '2026-08-16 09:10',
    detail: { items: [
      { '存货编码': 'CP001', '存货名称': '铝棒 Φ80', '规格型号': 'Φ80×3000', '仓库': '成品仓', '项目': '铝棒深加工', '数量': 200, '销售单位': '件', '批号': 'P2026-0801', '现存量': 800, '销售单位结存成本': 10.2, '税率%': 13, '单价': 15.5, '含税单价': 17.52, '金额': 3100, '税额': 404, '含税金额': 3504, '折扣%': 0, '折扣金额': 0 },
      { '存货编码': 'CP002', '存货名称': '铝板 6061', '规格型号': '1500×3000×2', '仓库': '成品仓', '项目': '铝棒深加工', '数量': 100, '销售单位': '件', '批号': 'P2026-0801', '现存量': 450, '销售单位结存成本': 8.6, '税率%': 13, '单价': 12.8, '含税单价': 14.46, '金额': 1280, '税额': 166, '含税金额': 1446, '折扣%': 5, '折扣金额': 64 },
      { '存货编码': 'CP004', '存货名称': '减速箱体 A', '规格型号': 'JS-400', '仓库': '成品仓', '项目': '铝棒深加工', '数量': 50, '销售单位': '件', '批号': 'B2026-0802', '现存量': 120, '销售单位结存成本': 210, '税率%': 13, '单价': 260, '含税单价': 293.8, '金额': 13000, '税额': 1690, '含税金额': 14690, '折扣%': 0, '折扣金额': 0 },
    ] },
  },
  {
    '编号': 'XS-2026-08-0002', '单据日期': '2026-08-19', '单据编号': 'XS-2026-08-0002', '单据状态': '草稿',
    '业务类型': '普通销售', '客户': '中天精工', '客户编码': 'KH002', '结算客户': '中天精工', '部门': '销售二部', '经手人': '李娜',
    '项目': '散热片批量', '仓库': '成品仓', '结算方式': '月结', '发票号码': '', '备注': '',
    '制单人': 'admin', '创建时间': '2026-08-19 14:00', '更新时间': '2026-08-19 14:00',
    detail: { items: [
      { '存货编码': 'CP003', '存货名称': '铝型材-散热片', '规格型号': 'XD-6063-T5', '仓库': '成品仓', '项目': '散热片批量', '数量': 300, '销售单位': '件', '批号': 'P2026-0819', '现存量': 600, '销售单位结存成本': 18.4, '税率%': 13, '单价': 22.6, '含税单价': 25.54, '金额': 6780, '税额': 882, '含税金额': 7662, '折扣%': 0, '折扣金额': 0 },
    ] },
  },
]