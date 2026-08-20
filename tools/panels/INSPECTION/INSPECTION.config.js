// ==================== 来料/成品检验单 #QM15（INSPECTION）2026-08-19 真实 T+ 抓取一比一复刻 ====================
// 数据来源：docs/ref/tplus-live/mech-20260819/qc/inspection-list.dom.json
//           （https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=QM&mId=QM15&pId=voucherView，真实面板码 #QM15，
//            名称「来料/成品检验单」，rowCount 505，明细页签=明细/不合格处理）
// 结构对齐：frontend/src/business/engine.js 的 PU_ORDER_CONFIG 模板 + tools/panels/PU_IN/PU_IN.config.js 完整产出范例
//           （metadata.panelPageDto.tablePages[0].queryFields/gridTabs + formPages + panelButtons
//             + buttonGroups + dataSchema.fields + detail.tabs[0].fields + selectConfig）
// 规范依据：docs/页面开发规范.md（buttonGroups {name,actions}、数据契约、gridTabs 列=明细字段名）
//           + docs/design/面板交互设计规范.md（选单前提、单据编号标准、工具栏标准、删除组、Excel 导入）
// 本文件为独立产出（不修改共享代码），可直接拼入 engine.js：置于 `const today`（第 54 行）之后任意位置，
// 例如 PU_IN_CONFIG 附近；引用 today 变量（合并后由 engine.js 顶层声明提供）。
// 合并接入清单（panelOf/nextNoFor/flattenFor/menus.js/编号前缀 JY-/init.sql）见 INSPECTION.notes.md。
// 角色：本面板为「选单源单据面板」——采购入库单（PURCHASE_IN）工具栏「选单 → 选检验单」的数据来源
//       （选单弹窗按 {panelCode:'INSPECTION', condition:{单据状态:'已审核'}} 拉取，故种子含一张已审核单据）；
//       本面板自身「选单 → 选到货单」以 ARRIVAL_IN（到货单）为拉式选单源。

const INSPECTION_CONFIG = {
  metadata: {
    panelCode: 'INSPECTION',
    panelName: '来料/成品检验单',
    panelCategory: '单据',
    autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止'] },
    panelPageDto: {
      tablePages: [{
        tableName: '检验单列表',
        queryFields: [
          // 真实 T+ 查询区 11 项（*单据日期/*单据编号/*业务类型/检验部门/检验员/检验合格数量自动入库/
          //   让步接收数量自动入库/报废数量自动入库/供应商/采购订单号/加工单号），
          // 按任务 5-6 个取核心 6 项：日期/编号/业务类型 + 检验类别（任务指定，替代 检验部门）/供应商/检验员
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '业务类型', dataType: '下拉框', options: ['来料检验', '成品检验'] },
          { dataName: '检验类别', dataType: '下拉框', options: ['来料检验', '成品检验', '首件检验'] },
          { dataName: '供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称' },
          { dataName: '检验员', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称' },
        ],
        gridTabs: [
          // 明细页签：真实可见列中选 18 个（dom.json cols hidden=false 顺序截取，全部 ⊆ detail.tabs[0].fields，
          // 规范 §八.8.1：gridTabs 列名 = 明细字段名，列与明细字段对不上会空白）
          { label: '明细', rowSource: 'detail', columns: ['存货编码', '存货名称', '规格型号', '检验批号', '仓库', '检验方式', '抽检比例%', '计量单位', '报检数量', '检验数量', '合格数量', '不合格数量', '合格率', '检验结果判定', '检验员', '检验日期', '检验项目', '判定'] },
          // 汇总页签：按 存货名称 分组（groupKeyOf 命中），报检/检验/合格/不合格数量 数值求和
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['存货名称', '规格型号', '计量单位', '报检数量', '检验数量', '合格数量', '不合格数量', '检验结果判定'] },
        ],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        rowOperationBarBtn: [],
        events: [],
      }],
      formPages: [{
        formName: '来料/成品检验单',
        fieldNames: '单据日期,单据编号,业务类型,检验类别,供应商编码,供应商,仓库,检验员,经手人,项目,部门,来源单号,备注',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏（2026-08-19 实测 #QM15 顶层按钮序列）：
    //   新增 | 选单 | 保存 | 修改 | 删除 | 审核 | 弃审 | 弃审 | 审批情况 | 生单 | 变更 | 生成批号
    //   | 工具 | 联查 | 设置 | 打印 | 更多 | 更多 | 更多
    // 下拉全名取自抓取 topText（新增组含 引入常用单据；选单组=选到货单/选成品报检单；生单组=生成采购入库单等 6 项；
    //   生成批号组=生成明细批号/生成不合格品批号；工具组=现存量查询/变更历史；联查组=到货情况）；
    // 按 页面开发规范.md 补 查找(查找,刷新)/导入(下载导入模板,导入) 组；任务指定 弃审/审批情况 独立成组；
    // 未实现动作保留（点击走 engine.UNIMPLEMENTED 提示「演示环境暂未实现，界面与 T+ 保持一致」）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选到货单', '选成品报检单', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印', '设置默认功能'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      { name: '审核', actions: ['审核'] },
      { name: '弃审', actions: ['弃审'] },
      { name: '审批情况', actions: ['审批情况'] },
      { name: '生单', actions: ['生成采购入库单', '生成产成品入库单', '生成委外入库单', '生成到货退回单', '生成成品报检退回单（生产报检）', '生成成品报检退回单（委外报检）', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '生成批号', actions: ['生成明细批号', '生成不合格品批号', '设置默认功能'] },
      { name: '查找', actions: ['查找', '刷新'] },
      { name: '工具', actions: ['现存量查询', '变更历史', '设置默认功能'] },
      { name: '联查', actions: ['到货情况'] },
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
      // 真实 #QM15 业务类型 = 来料检验/成品检验（面板名「来料/成品检验单」）；检验类别 另含 首件检验
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '来料检验', options: ['来料检验', '成品检验'] },
      { dataName: '检验类别', dataType: '下拉框', isRequired: false, defaultValue: '来料检验', options: ['来料检验', '成品检验', '首件检验'] },
      { dataName: '供应商编码', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '供应商', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { '停用': false }, refMap: [{ from: '往来单位编码', to: '供应商编码' }], refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '停用'] },
      { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
      { dataName: '检验员', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
      { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { '停用': false } },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { '停用': false } },
      { dataName: '来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [
        { label: '报检数量合计', field: '报检数量' },
        { label: '检验数量合计', field: '检验数量' },
        { label: '合格数量合计', field: '合格数量' },
        { label: '不合格数量合计', field: '不合格数量' },
      ],
      calc: [
        // 合格率：合格数量 / 检验数量 × 100（evaluateExpr 除零保护 b===0→0，草稿行 检验数量 0 安全）
        { target: '合格率', formula: '合格数量 / 检验数量 * 100', round: 2 },
      ],
      // 18 个明细字段：参照真实可见列（*存货名称/仓库/检验方式/抽检比例%/*计量单位/报检数量/检验数量/
      //   检验结果判定/合格数量/不合格数量/让步接收/报废数量 等）；存货→参照 INV（refMap 带出 编码/规格/计量单位）、
      //   仓库→参照 WH、检验员→参照 EMP；
      // 检验批号=真实「批号(合格) Batch」、检验结果判定=真实 InspectState（检验结果+判定合并列）、
      //   检验日期=真实 检验项目明细 子表 InspectDateTime、检验项目/判定=真实 检验项目明细 子表（InspectProject/Singlejudgment 单项判定合格）
      fields: [
        { dataName: '存货编码', dataType: '文本' },
        { dataName: '存货名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { '停用': false }, refMap: [{ from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '计量单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '计量单位', '停用'] },
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '检验批号', dataType: '文本' },
        { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { '停用': false } },
        { dataName: '检验方式', dataType: '下拉框', isRequired: true, defaultValue: '全检', options: ['全检', '抽检'] },
        { dataName: '抽检比例%', dataType: '小数', isRequired: false, defaultValue: 100 },
        { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升'] },
        { dataName: '报检数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '检验数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '合格数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '不合格数量', dataType: '小数', isRequired: false, defaultValue: 0 },
        { dataName: '合格率', dataType: '小数', computed: true },
        { dataName: '检验结果判定', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['合格', '不合格', '让步接收', '报废'] },
        { dataName: '检验员', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { '停用': false } },
        { dataName: '检验日期', dataType: '日期' },
        { dataName: '检验项目', dataType: '文本' },
        { dataName: '判定', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['合格', '不合格'] },
      ],
    }],
  },
  // 拉式选单（对齐 T+：#QM15 工具栏「选单 → 选到货单」，另有 选成品报检单 未建 source，走 UNIMPLEMENTED 提示）
  selectConfig: {
    source: 'ARRIVAL_IN',
    title: '选到货单',
    tip: '仅显示已审核且未中止的到货单，选中后明细带入检验单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '供应商', '仓库', '存货名称', '到货数量', '计量单位'],
    detailKey: 'items',
    headerMap: [
      { from: '单据编号', to: '来源单号' },
    ],
    detailMap: [
      { from: '存货名称', to: '存货名称' },
      { from: '到货数量', to: '报检数量' },
      { from: '计量单位', to: '计量单位' },
    ],
  },
}

// 来料/成品检验单演示数据（2 张：①已审核 3 行明细，供 采购入库单「选单 → 选检验单」作选单源；②草稿）
// 单据编号前缀 JY-（对齐 面板交互设计规范.md §9 单据编号标准：前缀-yyyy-MM-dd+序号；
// 编号前缀登记由主会话在 PxService.generateFormNo 完成，本文件仅使用约定前缀）
// 存货与 INV 种子一致（铝棒 Φ80=CP001 / 6061铝锭=CL002 / 切削液=CL004 / 包装木箱=CL005，见 engine.js INV_SEED）
// 数值自洽：合格率 = 合格数量 / 检验数量 × 100 四舍五入 2 位（95.83 = 460/480*100）
let INSPECTION_ROWS = [
  {
    '编号': 'JY-2026-08-0001', '单据编号': 'JY-2026-08-0001', '单据状态': '已审核', '审批状态': '已审批',
    '单据日期': '2026-08-14', '业务类型': '来料检验', '检验类别': '来料检验',
    '供应商编码': 'KH001', '供应商': '华东铝业', '仓库': '原料仓',
    '检验员': '赵刚', '经手人': '张伟', '项目': '铝棒采购', '部门': '质检部',
    '来源单号': 'ARR-2026-08-0001', '备注': '2026-08 铝材来料检验',
    '制单人': 'admin', '审核人': '系统管理员', '审核日期': '2026-08-14', '审核时间': '2026-08-14 15:30', '打印次数': 0,
    '创建时间': '2026-08-14 09:00', '更新时间': '2026-08-14 15:35', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '存货编码': 'CP001', '存货名称': '铝棒 Φ80', '规格型号': 'Φ80×3000', '检验批号': 'B20260801', '仓库': '原料仓', '检验方式': '全检', '抽检比例%': 100, '计量单位': '件', '报检数量': 200, '检验数量': 200, '合格数量': 200, '不合格数量': 0, '合格率': 100, '检验结果判定': '合格', '检验员': '赵刚', '检验日期': '2026-08-14', '检验项目': '尺寸公差', '判定': '合格' },
      { '存货编码': 'CL002', '存货名称': '6061铝锭', '规格型号': 'A00', '检验批号': 'B20260802', '仓库': '原料仓', '检验方式': '抽检', '抽检比例%': 20, '计量单位': 'kg', '报检数量': 500, '检验数量': 480, '合格数量': 460, '不合格数量': 20, '合格率': 95.83, '检验结果判定': '不合格', '检验员': '赵刚', '检验日期': '2026-08-14', '检验项目': '化学成分', '判定': '不合格' },
      { '存货编码': 'CL004', '存货名称': '切削液', '规格型号': '20L/桶', '检验批号': 'B20260803', '仓库': '辅料仓', '检验方式': '全检', '抽检比例%': 100, '计量单位': '升', '报检数量': 20, '检验数量': 20, '合格数量': 18, '不合格数量': 2, '合格率': 90, '检验结果判定': '让步接收', '检验员': '赵刚', '检验日期': '2026-08-14', '检验项目': '外观与包装', '判定': '合格' },
    ] },
  },
  {
    '编号': 'JY-2026-08-0002', '单据编号': 'JY-2026-08-0002', '单据状态': '草稿',
    '单据日期': today, '业务类型': '成品检验', '检验类别': '成品检验',
    '供应商编码': '', '供应商': '', '仓库': '成品仓',
    '检验员': '赵刚', '经手人': '李娜', '项目': '铝棒加工订单', '部门': '质检部',
    '来源单号': '', '备注': '',
    '制单人': 'admin', '审核人': '', '审核日期': '', '审核时间': '', '打印次数': 0,
    '创建时间': today + ' 10:00', '更新时间': today + ' 10:00', '发起人编号': 'tplusdemo12853',
    detail: { items: [
      { '存货编码': 'CL005', '存货名称': '包装木箱', '规格型号': '1200×800', '检验批号': '', '仓库': '成品仓', '检验方式': '全检', '抽检比例%': 100, '计量单位': '件', '报检数量': 100, '检验数量': 0, '合格数量': 0, '不合格数量': 0, '合格率': 0, '检验结果判定': '', '检验员': '', '检验日期': '', '检验项目': '', '判定': '' },
    ] },
  },
]

// 供验证脚本 require 使用（engine.js 合并时删除本行，取用模块级 const/let 声明）
if (typeof module !== 'undefined' && module.exports) { module.exports = { INSPECTION_CONFIG, INSPECTION_ROWS } }