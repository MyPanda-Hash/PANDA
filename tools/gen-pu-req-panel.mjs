// 生成请购单 PU_REQ 面板配置 SQL（对齐 docs/design/T+请购单-真实面板设计.md）
import fs from 'node:fs'
const invRef = (name, field, display, extra = {}) => ({ dataName: name, dataType: '参照', refPanel: 'INV', refField: field, displayField: display, filter: { 停用: false }, defaultValue: '', ...extra })
const cfg = {
  metadata: {
    version: '1.0', panelCode: 'PU_REQ', panelName: '请购单', panelCategory: '单据', autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '审批中', '已中止'] },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }, { buttonName: '保存' },
      { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
      { buttonName: '提交审批' }, { buttonName: '审批通过' }, { buttonName: '驳回审批' },
    ],
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选销售订单', '设置默认功能'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印'] },
      { name: '删除', actions: ['删除', '删除单据'] },
      { name: '审核', actions: ['审核', '提交审批', '审批通过', '审批驳回', '审批情况', '弃审'] },
      { name: '审批', actions: ['提交审批', '审批通过', '驳回审批'] },
      { name: '生单', actions: ['生成采购订单', '设置默认功能'] },
      { name: '变更', actions: ['变更'] },
      { name: '工具', actions: ['现存量查询', '变更历史', '联查', '生单流程联查'] },
      { name: '联查', actions: ['采购订单情况'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出'] },
      { name: '导入', actions: ['下载导入模板', '导入'] },
      { name: '更多', actions: ['复制', '放弃', '草稿', '附件', '刷新', '消息'] },
    ],
    panelPageDto: {
      tablePages: [{
        tableName: '请购单列表', rowOperationBarBtn: [], events: [],
        topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
        queryFields: [
          { dataName: '单据日期', dataType: '日期' },
          { dataName: '单据编号', dataType: '文本' },
          { dataName: '部门', dataType: '参照', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称' },
          { dataName: '请购人', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称' },
          { dataName: '项目', dataType: '参照', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称' },
          { dataName: '建议供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称' },
          { dataName: '需求日期', dataType: '日期' },
          { dataName: '到货地址', dataType: '文本' },
          { dataName: '销售订单号', dataType: '文本' },
          { dataName: '外部单据号', dataType: '文本' },
        ],
        gridTabs: [
          { label: '明细', rowSource: 'detail', columns: ['存货编码', '存货名称', '规格型号', '版本号', '采购单位', '数量', '数量2', '建议供应商', '报价', '单价', '含税单价', '税率%', '金额', '含税金额', '需求日期', '来源单据', '来源单号', '现存量', '现存量说明', '是否带票', '备注'] },
          { label: '汇总', rowSource: 'detail', summary: true, columns: ['存货名称', '采购单位', '数量', '单价', '金额', '含税金额'] },
        ],
      }],
      formPages: [{
        formName: '请购单',
        fieldNames: '单据日期,单据编号,部门,请购人,项目,建议供应商编码,建议供应商,建议供应商简称,收货人,电话,需求日期,到货地址,销售订单号,外部单据号,来源单据,来源单号,折扣,总金额,含税总金额,备注',
        bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
        events: [],
      }],
    },
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: '2026-08-24' },
      { dataName: '单据编号', dataType: '文本', isRequired: true, autoCode: true, defaultValue: '' },
      { dataName: '部门', dataType: '参照', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { 停用: false }, isRequired: false, defaultValue: '' },
      { dataName: '请购人', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false }, isRequired: true, defaultValue: '' },
      { dataName: '项目', dataType: '参照', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { 停用: false }, isRequired: false, defaultValue: '' },
      { dataName: '建议供应商编码', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '建议供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false }, isRequired: false, defaultValue: '', refMap: [{ from: '往来单位编码', to: '建议供应商编码' }, { from: '往来单位简称', to: '建议供应商简称' }], refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '停用'] },
      { dataName: '建议供应商简称', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '收货人', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '电话', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '需求日期', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '到货地址', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '销售订单号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '外部单据号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '来源单据', dataType: '文本', isRequired: false, defaultValue: '', readonly: true },
      { dataName: '来源单号', dataType: '文本', isRequired: false, defaultValue: '', readonly: true },
      { dataName: '折扣', dataType: '小数', isRequired: false, defaultValue: 0 },
      { dataName: '总金额', dataType: '小数', isRequired: false, computed: true },
      { dataName: '含税总金额', dataType: '小数', isRequired: false, computed: true },
      { dataName: '备注', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [{
      key: 'items', label: '明细', isRequired: true,
      summaryItems: [
        { label: '数量合计', field: '数量' }, { label: '金额合计', field: '金额' }, { label: '含税金额合计', field: '含税金额' },
      ],
      calc: [
        { target: '含税单价', formula: '单价 * (1 + 税率% / 100)', round: 2 },
        { target: '金额', formula: '数量 * 单价', round: 2 },
        { target: '含税金额', formula: '数量 * 含税单价', round: 2 },
      ],
      fields: [
        invRef('存货编码', '存货编码', '存货名称', { isRequired: true, refMap: [{ from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '采购单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '计量单位', '停用'] }),
        invRef('存货名称', '存货名称', '存货名称', { isRequired: true, refColumns: ['存货编码', '存货名称', '规格型号', '计量单位', '停用'] }),
        { dataName: '规格型号', dataType: '文本' },
        { dataName: '版本号', dataType: '文本' },
        { dataName: '采购单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: ['件', 'kg', '套', '升', '台'] },
        { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
        { dataName: '数量2', dataType: '小数', defaultValue: 0 },
        { dataName: '建议供应商', dataType: '文本', defaultValue: '' },
        { dataName: '报价', dataType: '小数', defaultValue: 0 },
        { dataName: '单价', dataType: '小数', defaultValue: 0 },
        { dataName: '含税单价', dataType: '小数', computed: true },
        { dataName: '税率%', dataType: '小数', defaultValue: 13, options: [0, 3, 6, 9, 13] },
        { dataName: '金额', dataType: '小数', computed: true },
        { dataName: '含税金额', dataType: '小数', computed: true },
        { dataName: '需求日期', dataType: '日期' },
        { dataName: '来源单据', dataType: '文本' },
        { dataName: '来源单号', dataType: '文本' },
        { dataName: '现存量', dataType: '小数', computed: true },
        { dataName: '现存量说明', dataType: '文本', computed: true },
        { dataName: '是否带票', dataType: '下拉框', defaultValue: '不带票', options: ['带票', '不带票'] },
        { dataName: '备注', dataType: '文本' },
      ],
    }],
  },
  selectConfig: {
    source: 'SO_ORDER', title: '选销售订单',
    tip: '仅显示已审核且未中止的销售订单，选中后明细带入请购单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '业务员', '预计交货日期', '存货名称', '数量', '销售单位'],
    detailKey: 'items',
    headerMap: [{ from: '单据编号', to: '销售订单号' }, { from: '单据日期', to: '需求日期' }],
    detailMap: [
      { from: '存货编码', to: '存货编码' }, { from: '存货名称', to: '存货名称' },
      { from: '规格型号', to: '规格型号' }, { from: '销售单位', to: '采购单位' },
      { from: '数量', to: '数量' }, { from: '现存量', to: '现存量' },
    ],
  },
}
const esc = s => String(s).replace(/'/g, "''")
const json = JSON.stringify(cfg)
const sql = "INSERT INTO panel_config (panel_code, panel_name, category, config) VALUES ('PU_REQ', '请购单', '单据', '" + esc(json) + "') ON DUPLICATE KEY UPDATE config = VALUES(config);\n"
fs.writeFileSync('F:/INCER/light-mes/tools/pu_req-panel.sql', sql, 'utf8')
console.log('SQL written, config bytes:', json.length)