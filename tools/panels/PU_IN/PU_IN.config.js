// ==================== ⑧ 进货单 #PU03（PU_IN）2026-08-19 真实 T+ 抓取一比一复刻 ====================
// 数据来源：docs/ref/tplus-live/mech-20260819/src/purchase_in-list.dom.json
//           （https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=PU&mId=PU03&pId=voucherView，真实菜单码 #PU03）
// 结构对齐：frontend/src/business/engine.js 的 PU_ORDER_CONFIG 模板
//           （metadata.panelPageDto.tablePages[0].queryFields/gridTabs + formPages + panelButtons
//             + buttonGroups + dataSchema.fields + detail.tabs[0].fields）
// 规范依据：docs/页面开发规范.md（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名）
//           + docs/design/面板交互设计规范.md（选单前提、单据编号标准、工具栏标准、删除组、Excel 导入）
// 本文件为独立产出（不修改共享代码），可直接拼入 engine.js：置于 `const today`（第 54 行）之后任意位置，
// 例如 PU_ORDER_CONFIG 附近；引用 today 变量（合并后由 engine.js 顶层声明提供）。
// 合并接入清单（panelOf/nextNoFor/flattenFor/menus.js/编号前缀）见 PU_IN.notes.md。
// 角色：本面板为「选单源单据面板」——采购入库单（PURCHASE_IN）工具栏「选单 → 选进货单」的数据来源
//       （选单弹窗按 {panelCode:'PU_IN', condition:{单据状态:'已审核'}} 拉取，故种子含一张已审核单据）。

const PU_IN_CONFIG = {
  metadata: {
    panelCode: 'PU_IN',
    panelName: '进货单',
    panelCategory: '单据',
    autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '进货单列表',
        queryFields: [
          // 真实 T+ 查询区 8 项（*单据日期/*单据编号/*业务类型/*票据类型/供应商编码/*供应商/应付余额/*业务员），
          // 按任务 3-6 个取核心 6 项：日期/编号/业务类型 + 供应商/业务员/项目
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['普通采购', '采购退货'] },
          { dataName: '供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称' },
          { dataName: '业务员', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称' },
          { dataName: '项目', dataType: '参照', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称' },
        ],
        gridTabs: [
          // 明细页签：真实可见列中选 16 个（dom.json cols hidden=false 顺序截取，全部 ⊆ detail.tabs[0].fields，
          // 规范 §八.8.1：gridTabs 列名 = 明细字段名，列与明细字段对不上会空白）
          { label: '明细', rowSource: 'detail', columns: ['条形码', '仓库', '项目', '存货名称', '存货编码', '规格型号', '数量', '材质', '采购单位', '计量单位组合', '批号', '折扣%', '单价', '税率%', '含税单价', '金额'] },
          // 汇总页签：按 存货名称 分组（groupKeyOf 命中），数量/单价/金额/税额/含税金额 数值求和
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['仓库', '存货名称', '规格型号', '采购单位', '数量', '单价', '金额', '税额', '含税金额'] },
        ],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '进货单',
        fieldNames: '单据日期,单据编号,业务类型,票据类型,供应商编码,供应商,供应商简称,经手人,业务员,部门,项目,仓库,应付余额,备注',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏（2026-08-19 实测 #PU03 顶层按钮序列）：
    //   新增 | 选单 | 保存 | 修改 | 删除 | 审核 | 弃审 | 弃审 | 审批情况 | 生单 | 转换 | 生成凭证
    //   | 变更 | 生成批号 | 工具 | 联查 | 设置 | 打印 | 更多 | 智能 | 单据分享 | 更多
    // 下拉全名取自抓取 topText；按 页面开发规范.md 补 查找(查找,刷新)/导入(下载导入模板,导入) 组；
    // 未实现动作保留（点击走 engine.UNIMPLEMENTED 提示「演示环境暂未实现，界面与 T+ 保持一致」）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选采购订单', '选采购入库单', '选进货单（普通采购）', '选到货单', '选检验单', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印', '设置默认功能'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      { name: '审核', actions: ['审核', '弃审'] },
      { name: '生单', actions: ['生成采购发票（普通采购）', '生成采购发票（采购退货）', '生成采购入库单（普通采购）', '生成采购入库单（采购退货）', '生成进货单（采购退货）', '生成付款申请单', '设置默认功能'] },
      { name: '转换', actions: ['转成销售订单', '转成销货单', '转成销售出库单', '转成材料出库单', '设置默认功能'] },
      { name: '生成凭证', actions: ['生成凭证'] },
      { name: '变更', actions: ['变更'] },
      { name: '生成批号', actions: ['生成批号'] },
      { name: '查找', actions: ['查找', '刷新'] },
      { name: '工具', actions: ['费用单', '费用分摊单', '现存量查询', '拣货', '变更历史', '联查', '生单流程联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出'] },
      { name: '导入', actions: ['下载导入模板', '导入'] },
      { name: '更多', actions: ['复制', '导出', '退出', '放弃', '草稿', '附件', '刷新', '消息'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '普通采购', options: ['普通采购', '采购退货'] },
      { dataName: '票据类型', dataType: '下拉框', isRequired: false, defaultValue: '专用发票', options: ['专用发票', '普通发票', '农副产品发票', '收据'] },
      { dataName: '供应商编码', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '供应商', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false }, refMap: [{ from: '往来单位编码', to: '供应商编码' }, { from: '往来单位简称', to: '供应商简称' }, { from: '往来单位名称', to: '结算供应商' }], refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '结算客户', '分管部门', '停用'] },
      { dataName: '供应商简称', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '业务员', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '应付余额', dataType: '小数', isRequired: false, defaultValue: 0 },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [
        { label: '数量合计', field: '数量' },
        { label: '金额合计', field: '金额' },
        { label: '税额合计', field: '税额' },
        { label: '含税金额合计', field: '含税金额' },
      ],
      calc: [
        { target: '含税单价', formula: '单价 * (1 + 税率% / 100)', round: 2 },
        { target: '金额', formula: '数量 * 单价', round: 2 },
        { target: '税额', formula: '数量 * (含税单价 - 单价)', round: 2 },
        { target: '含税金额', formula: '金额 + 税额', round: 2 },
        { target: '计量单位组合', formula: '数量', round: 2 },
      ],
      // 18 个明细字段：参照真实可见列；存货→参照 INV（refMap 带出 编码/规格/采购单位）、仓库→参照 WH、项目→参照 PROJ；
      // 金额/税额/含税金额/含税单价 computed 走 calc 链（价税分离，对齐真实 T+ 进货单）
      fields: [
        { dataName: '条形码', dataType: '文本' },
        { dataName: '仓库', dataType: '参照', isRequired: true, defaultValue: '原料仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
        { dataName: '存货名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { '停用': false }, refMap: [{ from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '采购单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '计量单位', '停用'] },
        { dataName: '存货编码', dataType: '文本' },
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '材质', dataType: '下拉框', isRequired: false, defaultValue: '铝合金', options: ['铝合金', '不锈钢', '碳钢', '铜材', '化工品', '木质'] },
        { dataName: '采购单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升'] },
        { dataName: '计量单位组合', dataType: '文本', computed: true },
        { dataName: '批号', dataType: '文本' },
        { dataName: '折扣%', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '单价', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '税率%', dataType: '小数', isRequired: false, defaultValue: 13, options: [0, 3, 6, 9, 13] },
        { dataName: '含税单价', dataType: '小数', computed: true },
        { dataName: '金额', dataType: '小数', computed: true },
        { dataName: '税额', dataType: '小数', computed: true },
        { dataName: '含税金额', dataType: '小数', computed: true },
      ],
    }],
  },
  // 拉式选单（对齐 T+：进货单选采购订单；本面板同时作为 采购入库单「选进货单」的选单源）
  selectConfig: {
    source: 'PU_ORDER',
    title: '选采购订单',
    tip: '仅显示已审核且未中止的采购订单，选中后明细带入进货单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '供应商', '项目', '物料名称', '数量', '单位'],
    detailKey: 'items',
    headerMap: [
      { from: '单据编号', to: '采购订单号' },
      { from: '供应商', to: '供应商' },
      { from: '项目', to: '项目' },
    ],
    detailMap: [
      { from: '物料编码', to: '存货编码' },
      { from: '物料名称', to: '存货名称' },
      { from: '规格型号', to: '规格型号' },
      { from: '单位', to: '采购单位' },
      { from: '数量', to: '数量' },
      { from: '单价', to: '单价' },
      { from: '税率%', to: '税率%' },
      { from: '含税单价', to: '含税单价' },
      { from: '金额', to: '金额' },
      { from: '含税金额', to: '含税金额' },
      { from: '仓库', to: '仓库' },
      { from: '现存量', to: '现存量' },
    ],
  },
}

// 进货单演示数据（2 张：①已审核 3 行明细，供 采购入库单「选单 → 选进货单」作选单源；②草稿）
// 单据编号前缀 PU-（对齐 面板交互设计规范.md §9 单据编号标准：前缀-yyyy-MM-dd+序号；
// 编号前缀登记由主会话在 PxService.generateFormNo 完成，本文件仅使用约定前缀）
// 金额一致性：含税单价 = 单价*(1+税率%) 四舍五入 2 位；税额 = 数量*(含税单价-单价)；含税金额 = 金额+税额
let PU_IN_ROWS = [
  {
    '编号': 'PU-2026-08-0001', '单据编号': 'PU-2026-08-0001', '单据状态': '已审核', '审批状态': '已审批',
    '单据日期': '2026-08-14', '业务类型': '普通采购', '票据类型': '专用发票',
    '供应商编码': 'KH001', '供应商': '华东铝业', '供应商简称': '华东铝业', '结算供应商': '华东铝业',
    '经手人': '张伟', '业务员': '张伟', '部门': '采购部', '项目': '铝棒采购', '仓库': '原料仓',
    '应付余额': 12500, '备注': '2026-08 铝材采购到货',
    '制单人': 'admin', '审核人': '系统管理员', '审核日期': '2026-08-14', '审核时间': '2026-08-14 09:30', '打印次数': 0,
    '创建时间': '2026-08-14 09:00', '更新时间': '2026-08-14 09:35', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '条形码': '6901234567890', '仓库': '原料仓', '项目': '铝棒采购', '存货名称': '铝棒 Φ80', '存货编码': 'CP001', '规格型号': 'Φ80×3000', '数量': 200, '材质': '铝合金', '采购单位': '件', '计量单位组合': '200件', '批号': 'B20260801', '折扣%': 0, '单价': 15.5, '税率%': 13, '含税单价': 17.52, '金额': 3100, '税额': 404, '含税金额': 3504, '到货日期': '2026-08-15', '采购订单号': 'PO-2026-08-0001', '现存量': 800, '现存量说明': '充足', '备注': '' },
      { '条形码': '6901234567891', '仓库': '原料仓', '项目': '铝棒采购', '存货名称': '6061铝锭', '存货编码': 'CL002', '规格型号': 'A00', '数量': 500, '材质': '铝合金', '采购单位': 'kg', '计量单位组合': '500kg', '批号': 'B20260802', '折扣%': 0, '单价': 12.8, '税率%': 13, '含税单价': 14.46, '金额': 6400, '税额': 830, '含税金额': 7230, '到货日期': '2026-08-16', '采购订单号': 'PO-2026-08-0001', '现存量': 6000, '现存量说明': '充足', '备注': '' },
      { '条形码': '6901234567892', '仓库': '辅料仓', '项目': '铝棒采购', '存货名称': '切削液', '存货编码': 'CL004', '规格型号': '20L/桶', '数量': 20, '材质': '化工品', '采购单位': '升', '计量单位组合': '20升', '批号': '', '折扣%': 0, '单价': 18, '税率%': 13, '含税单价': 20.34, '金额': 360, '税额': 46.8, '含税金额': 406.8, '到货日期': '2026-08-17', '采购订单号': 'PO-2026-08-0001', '现存量': 260, '现存量说明': '充足', '备注': '' },
    ] },
  },
  {
    '编号': 'PU-2026-08-0002', '单据编号': 'PU-2026-08-0002', '单据状态': '草稿',
    '单据日期': today, '业务类型': '普通采购', '票据类型': '普通发票',
    '供应商编码': 'KH002', '供应商': '中天精工', '供应商简称': '中天精工', '结算供应商': '中天精工',
    '经手人': '李娜', '业务员': '李娜', '部门': '采购部', '项目': '包装材料采购', '仓库': '辅料仓',
    '应付余额': 0, '备注': '',
    '制单人': 'admin', '审核人': '', '审核日期': '', '审核时间': '', '打印次数': 0,
    '创建时间': today + ' 10:00', '更新时间': today + ' 10:00', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '条形码': '6901234567899', '仓库': '辅料仓', '项目': '包装材料采购', '存货名称': '包装木箱', '存货编码': 'CL005', '规格型号': '1200×800', '数量': 100, '材质': '木质', '采购单位': '件', '计量单位组合': '100件', '批号': '', '折扣%': 0, '单价': 5, '税率%': 13, '含税单价': 5.65, '金额': 500, '税额': 65, '含税金额': 565, '到货日期': '', '采购订单号': '', '现存量': 300, '现存量说明': '充足', '备注': '' },
    ] },
  },
]