// ==================== 到货单 #QM05（ARRIVAL_IN）2026-08-19 真实 T+ 抓取一比一复刻 ====================
// 数据来源：docs/ref/tplus-live/mech-20260819/qc/arrival_in-list.dom.json
//           （https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=QM&mId=QM05&pId=voucherView，真实菜单码 #QM05，质量管理模块 sysId=QM）
// 结构对齐：frontend/src/business/engine.js 的 PU_ORDER_CONFIG / PU_IN_CONFIG 模板
//           （metadata.panelPageDto.tablePages[0].queryFields/gridTabs + formPages + panelButtons
//             + buttonGroups + dataSchema.fields + detail.tabs[0].fields + selectConfig）
// 规范依据：docs/页面开发规范.md（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名）
//           + docs/design/面板交互设计规范.md（§3 选单前提/§9 单据编号标准/§12 删除组/§18 Excel 导入）
// 本文件为独立产出（不修改共享代码），可直接拼入 engine.js：置于 `const today`（第 54 行）之后任意位置，
// 例如 PU_ORDER_CONFIG 附近；引用 today 变量（合并后由 engine.js 顶层声明提供）。
// 合并接入清单（panelOf/nextNoFor/flattenFor/menus.js/编号前缀 DH-）见 ARRIVAL_IN.notes.md。
// 角色：本面板为「选单源单据面板」——采购入库单（PURCHASE_IN）工具栏「选单 → 选到货单」的数据来源
//       （选单弹窗按 {panelCode:'ARRIVAL_IN', condition:{单据状态:'已审核'}} 拉取，故种子含一张已审核单据）。

const ARRIVAL_IN_CONFIG = {
  metadata: {
    panelCode: 'ARRIVAL_IN',
    panelName: '到货单',
    panelCategory: '单据',
    autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '到货单列表',
        queryFields: [
          // 真实 T+ 查询区 5 项（*单据日期/*单据编号/*业务类型/*供应商/业务员），
          // 按任务 5-6 个取全 5 项 + 补 项目（参照 PROJ，实用查询，参照 PU_IN 补法）
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['普通采购', '采购退货'] },
          { dataName: '供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称' },
          { dataName: '业务员', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称' },
          { dataName: '项目', dataType: '参照', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称' },
        ],
        gridTabs: [
          // 明细页签：真实可见列中选 18 个核心列（dom.json cols hidden=false 顺序截取 + 价税列），
          // 全部 ⊆ detail.tabs[0].fields（20 个），规范 §八.8.1：gridTabs 列名 = 明细字段名，对不上会空白
          { label: '明细', rowSource: 'detail', columns: ['项目', '存货编码', '存货名称', '规格型号', '仓库', '采购单位', '到货数量', '到货拒收数量', '实收数量', '预计到货日期', '检验方式', '抽检比例%', '采购订单号', '单价', '税率%', '含税单价', '金额', '含税金额'] },
          // 汇总页签：按 存货名称 分组（groupKeyOf 命中），数量/金额 数值求和
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['仓库', '存货名称', '规格型号', '采购单位', '到货数量', '实收数量', '单价', '金额', '含税金额'] },
        ],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '到货单',
        fieldNames: '单据日期,单据编号,业务类型,供应商编码,供应商,供应商简称,仓库,经手人,业务员,部门,项目,到货日期,来源单号,备注',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏（2026-08-19 实测 #QM05 顶层按钮序列）：
    //   新增 | 选单 | 保存 | 修改 | 删除 | 审核 | 弃审 | 弃审 | 审批情况 | 生单 | 变更 | 生成批号
    //   | 工具 | 联查 | 设置 | 打印 | 更多 | 更多
    // 下拉全名取自抓取 topText；按 页面开发规范.md 补 查找(查找,刷新)/导入(下载导入模板,导入) 组；
    // 未实现动作保留（点击走 engine.UNIMPLEMENTED 提示「演示环境暂未实现，界面与 T+ 保持一致」）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选采购订单', '选检验单', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印', '设置默认功能'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      // 任务指定三态（草稿/已审核/已中止）→ 审核/弃审 直接过审；审批情况 保留（真实 topText 有）
      { name: '审核', actions: ['审核', '弃审', '审批情况'] },
      { name: '生单', actions: ['生成检验单', '生成采购入库单', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '生成批号', actions: ['生成批号'] },
      { name: '查找', actions: ['查找', '刷新'] },
      { name: '工具', actions: ['现存量查询', '变更历史'] },
      { name: '联查', actions: ['采购订单情况', '检验情况', '入库情况', '生单流程联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出', '明细标签打印'] },
      { name: '导入', actions: ['下载导入模板', '导入'] },
      { name: '更多', actions: ['复制', '放弃', '草稿', '附件', '刷新', '消息'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '普通采购', options: ['普通采购', '采购退货'] },
      { dataName: '供应商编码', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '供应商', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false }, refMap: [{ from: '往来单位编码', to: '供应商编码' }, { from: '往来单位简称', to: '供应商简称' }, { from: '往来单位名称', to: '结算供应商' }], refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '结算客户', '分管部门', '停用'] },
      { dataName: '供应商简称', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '业务员', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '到货日期', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [
        { label: '到货数量合计', field: '到货数量' },
        { label: '实收数量合计', field: '实收数量' },
        { label: '金额合计', field: '金额' },
        { label: '含税金额合计', field: '含税金额' },
      ],
      // 价税分离（对齐真实 T+ 进货单/到货单口径；金额按 到货数量 计价，拒收部分在入库/开票环节冲减）
      calc: [
        { target: '含税单价', formula: '单价 * (1 + 税率% / 100)', round: 2 },
        { target: '金额', formula: '到货数量 * 单价', round: 2 },
        { target: '税额', formula: '到货数量 * (含税单价 - 单价)', round: 2 },
        { target: '含税金额', formula: '金额 + 税额', round: 2 },
      ],
      // 20 个明细字段：参照真实可见列；存货→参照 INV（refMap 带出 编码/规格/采购单位）、仓库→参照 WH、项目→参照 PROJ；
      // 金额/税额/含税金额/含税单价 computed 走 calc 链
      fields: [
        { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
        { dataName: '存货编码', dataType: '文本' },
        { dataName: '存货名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { '停用': false }, refMap: [{ from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '采购单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '计量单位', '停用'] },
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '仓库', dataType: '参照', isRequired: true, defaultValue: '原料仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '采购单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升'] },
        { dataName: '到货数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '到货拒收数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '实收数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '预计到货日期', dataType: '日期', isRequired: false, defaultValue: '' },
        { dataName: '检验方式', dataType: '下拉框', isRequired: true, defaultValue: '抽检', options: ['全检', '抽检', '免检'] },
        { dataName: '抽检比例%', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '检验要求', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '采购订单号', dataType: '文本', isRequired: false, defaultValue: '' },
        { dataName: '单价', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '税率%', dataType: '小数', isRequired: false, defaultValue: 13, options: [0, 3, 6, 9, 13] },
        { dataName: '含税单价', dataType: '小数', computed: true },
        { dataName: '金额', dataType: '小数', computed: true },
        { dataName: '税额', dataType: '小数', computed: true },
        { dataName: '含税金额', dataType: '小数', computed: true },
      ],
    }],
  },
  // 拉式选单（对齐 T+ topText：到货单「选单 → 选采购订单」；本面板同时作为 采购入库单「选到货单」的选单源）
  selectConfig: {
    source: 'PU_ORDER',
    title: '选采购订单',
    tip: '仅显示已审核且未中止的采购订单，选中后明细带入到货单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '供应商', '币种', '汇率', '物料名称', '数量', '单位', '预计到货日期'],
    detailKey: 'items',
    headerMap: [
      { from: '单据编号', to: '来源单号' },
      { from: '供应商', to: '供应商' },
      { from: '项目', to: '项目' },
    ],
    detailMap: [
      { from: '物料编码', to: '存货编码' },
      { from: '物料名称', to: '存货名称' },
      { from: '规格型号', to: '规格型号' },
      { from: '单位', to: '采购单位' },
      { from: '数量', to: '到货数量' },
      { from: '预计到货日期', to: '预计到货日期' },
      { from: '单价', to: '单价' },
      { from: '税率%', to: '税率%' },
      { from: '含税单价', to: '含税单价' },
      { from: '金额', to: '金额' },
      { from: '含税金额', to: '含税金额' },
      { from: '仓库', to: '仓库' },
    ],
  },
}

// 到货单演示数据（2 张：①已审核 3 行明细，供 采购入库单「选单 → 选到货单」作选单源；②草稿）
// 单据编号前缀 DH-（对齐 面板交互设计规范.md §9 单据编号标准：前缀-yyyy-MM-dd+序号；
// 编号前缀登记由主会话在 PxService.generateFormNo 完成，本文件仅使用约定前缀）
// 金额一致性：含税单价 = 单价*(1+税率%) 四舍五入 2 位；金额 = 到货数量*单价；
//   税额 = 到货数量*(含税单价-单价)；含税金额 = 金额+税额（与 engine.js 既有 SO/PU_IN 种子同口径）
let ARRIVAL_IN_ROWS = [
  {
    '编号': 'DH-2026-08-0001', '单据编号': 'DH-2026-08-0001', '单据状态': '已审核', '审批状态': '已审批',
    '单据日期': '2026-08-15', '业务类型': '普通采购',
    '供应商编码': 'KH001', '供应商': '华东铝业', '供应商简称': '华东铝业', '结算供应商': '华东铝业',
    '仓库': '原料仓', '经手人': '张伟', '业务员': '张伟', '部门': '采购部', '项目': '铝棒采购',
    '到货日期': '2026-08-15', '来源单号': 'PO-2026-08-0001', '备注': '2026-08 铝材到货待检验',
    '制单人': 'admin', '审核人': '系统管理员', '审核日期': '2026-08-15', '审核时间': '2026-08-15 09:00', '打印次数': 0,
    '创建时间': '2026-08-15 08:30', '更新时间': '2026-08-15 09:05', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '项目': '铝棒采购', '存货编码': 'CP001', '存货名称': '铝棒 Φ80', '规格型号': 'Φ80×3000', '仓库': '原料仓', '采购单位': '件', '到货数量': 200, '到货拒收数量': 2, '实收数量': 198, '预计到货日期': '2026-08-20', '检验方式': '抽检', '抽检比例%': 10, '检验要求': '按 GB/T 3190 标准检验', '采购订单号': 'PO-2026-08-0001', '单价': 15.5, '税率%': 13, '含税单价': 17.52, '金额': 3100, '税额': 404, '含税金额': 3504 },
      { '项目': '铝棒采购', '存货编码': 'CP002', '存货名称': '铝板 6061', '规格型号': '1500×3000×2', '仓库': '原料仓', '采购单位': '件', '到货数量': 100, '到货拒收数量': 0, '实收数量': 100, '预计到货日期': '2026-08-22', '检验方式': '全检', '抽检比例%': 100, '检验要求': '外观及尺寸全检', '采购订单号': 'PO-2026-08-0001', '单价': 12.8, '税率%': 13, '含税单价': 14.46, '金额': 1280, '税额': 166, '含税金额': 1446 },
      { '项目': '散热片批量', '存货编码': 'CP003', '存货名称': '铝型材-散热片', '规格型号': 'XD-6063-T5', '仓库': '原料仓', '采购单位': '件', '到货数量': 300, '到货拒收数量': 4, '实收数量': 296, '预计到货日期': '2026-08-25', '检验方式': '抽检', '抽检比例%': 5, '检验要求': '散热性能抽检', '采购订单号': 'PO-2026-08-0002', '单价': 22.6, '税率%': 13, '含税单价': 25.54, '金额': 6780, '税额': 882, '含税金额': 7662 },
    ] },
  },
  {
    '编号': 'DH-2026-08-0002', '单据编号': 'DH-2026-08-0002', '单据状态': '草稿',
    '单据日期': today, '业务类型': '普通采购',
    '供应商编码': 'KH002', '供应商': '中天精工', '供应商简称': '中天精工', '结算供应商': '中天精工',
    '仓库': '原料仓', '经手人': '李娜', '业务员': '李娜', '部门': '采购部', '项目': '铝板采购',
    '到货日期': '', '来源单号': '', '备注': '',
    '制单人': 'admin', '审核人': '', '审核日期': '', '审核时间': '', '打印次数': 0,
    '创建时间': today + ' 10:00', '更新时间': today + ' 10:00', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '项目': '铝板采购', '存货编码': 'CP002', '存货名称': '铝板 6061', '规格型号': '1500×3000×2', '仓库': '原料仓', '采购单位': '件', '到货数量': 150, '到货拒收数量': 0, '实收数量': 150, '预计到货日期': '2026-08-28', '检验方式': '全检', '抽检比例%': 100, '检验要求': '', '采购订单号': '', '单价': 12.8, '税率%': 13, '含税单价': 14.46, '金额': 1920, '税额': 249, '含税金额': 2169 },
    ] },
  },
]

// 供独立验证（node --check / require 校验结构）；合并入 engine.js 时删除本行，以 const 声明为准
module.exports = { ARRIVAL_IN_CONFIG, ARRIVAL_IN_ROWS }