import request from '@core/request'
import { USE_MOCK, USE_PANELX } from '@core/env'
import { initSdk, requireAuthed } from '@core/sdk'
import {
  unwrap, errMsg, mapType, parseJson, adaptPanelConfig, adaptMeta,
  adaptFormData, panelxButtonGroups, platformCall,
} from '@core/panel-engine'

// 通用层函数继续对外导出（保持既有调用方兼容）
export { unwrap, errMsg }

// ==================== 业务层：PanelX 平台直连/代理模式 ====================
// 本地菜单/路由使用自有面板码（MANU_ORDER），平台侧对应真实面板 SdkTest_IML_00002
const PANEL_MAP = { MANU_ORDER: 'SdkTest_IML_00002' }

export function resolvePanelCode(panelCode) {
  if (!USE_PANELX) return panelCode
  return PANEL_MAP[panelCode] || panelCode
}

// 面板配置缓存（平台 getPanelConfig 相对重，列表页/表单页共用）
const _platformCfgCache = {}

async function platformConfig(panelCode) {
  const code = resolvePanelCode(panelCode)
  if (_platformCfgCache[code]) return _platformCfgCache[code]
  const sdk = await requireAuthed()
  // 注意：SDK 的 getPanelConfig 接收字符串面板码（传对象会被序列化进 query，后端 400）；
  // 返回 {state,msg,data:{metadata,dataSchema}} 包装，需取 .data
  const cfg = (await platformCall((sd) => sd.api.getPanelConfig(code)))?.data
  const adapted = adaptPanelConfig(cfg, panelCode)
  _platformCfgCache[code] = adapted
  return adapted
}

// 确保平台模式已登录；非平台模式（mock / 本地后端）直接通过
export async function ensurePanelx() {
  if (!USE_PANELX) return null
  initSdk()
  return requireAuthed()
}
// ==================== mock 数据（与后端 init.sql 面板配置一致） ====================
// MANU_ORDER 面板 2026-08-14 按实测 T+ 轻MES 生产加工单一比一重做：
// 表头 23 字段（锭号=自动单号、合同号=日期型） + 三明细分录（各带 明细/汇总 子页签）
// 产成品明细 21 列 / 材料明细 24 列（可见）/ 工序明细 44 列 + 工序材料子表 7 列
// 工具栏 T+ 分组：新增/选单/保存/删除/审核/生单/变更/工具/设置/打印/更多

const today = new Date().toISOString().slice(0, 10)

const PROCESS_OPTIONS = ['下料', '车削', '铣削', '钻孔', '热处理', '装配', '检验']
const WORKSHOP_OPTIONS = ['熔铸车间', '轧制车间', '精整车间', '测试车间']

const MOCK_CONFIG = {
  metadata: {
    panelCode: 'MANU_ORDER',
    panelName: '生产加工单',
    panelCategory: '单据',
    panelState: {
      dataName: '单据状态',
      dataType: 'STRING',
      defaultOptions: ['草稿', '已审核', '生产中', '已完工', '已中止', '已关闭'],
    },
    panelPageDto: {
      tablePages: [
        {
          tableName: '生产加工单列表',
          queryFields: [
            { dataName: '合同号', dataType: '日期' },
            { dataName: '锭号', dataType: '文本' },
            { dataName: '批号', dataType: '下拉框', options: ['正常', '加急', '特急'] },
            { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS },
            { dataName: '预开工日', dataType: '日期' },
            { dataName: '预完工日', dataType: '日期' },
            { dataName: '销售订单号', dataType: '文本' },
            { dataName: '客户编码', dataType: '下拉框', options: ['KH001', 'KH002', 'KH003', 'KH004', 'KH005'] },
            { dataName: '客户', dataType: '文本' },
            { dataName: '测试程序', dataType: '下拉框', options: ['光谱分析', '硬度测试', '拉伸测试', '金相检验', '超声波探伤'] },
            { dataName: '生产订单客户', dataType: '文本' },
          ],
          gridTabs: [
            { label: '产成品明细', rowSource: 'products', columns: ['生产类型', '产品编码', '存货图片', '产品名称', '规格型号', '型号', '适用BOM', 'BOM展开方式', '生产单位', '数量', '齐套数量(主)', '累计汇报套数(工序单位)', '可用量', '可用量说明', '现存量', '现存量说明', '产品字符公用自定义项1', '图号', '单重', '总重', '需求令号'] },
            { label: '产成品明细汇总', rowSource: 'products', summary: true, columns: ['生产类型', '产品编码', '存货图片', '产品名称', '规格型号', '型号', '适用BOM', 'BOM展开方式', '生产单位', '数量', '齐套数量(主)', '累计汇报套数(工序单位)', '可用量', '可用量说明', '现存量', '现存量说明', '产品字符公用自定义项1', '图号', '单重', '总重', '需求令号'] },
          ],
          topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
          rowOperationBarBtn: [],
          events: [],
        },
      ],
      formPages: [
        {
          formName: '生产加工单',
          fieldNames: '合同号,锭号,批号,生产车间,预开工日,预完工日,销售订单号,客户编码,客户,测试程序,生产订单客户,机构,重量,开工日期,完工日期,启用派工,自动转移,产品自动添加到材料,是否手工修改单据编码,外部单据号,负责人,启用领料申请,对方仓库',
          bottomOperationBarBtn: [
            { buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' },
            { buttonName: '弃审' }, { buttonName: '中止执行' }, { buttonName: '草稿' }, { buttonName: '放弃' },
          ],
          events: [],
        },
      ],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' },
      { buttonName: '中止执行' }, { buttonName: '草稿' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏分组（主按钮 + 下拉项）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选单', '选销售订单'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印'] },
      { name: '删除', actions: ['删除'] },
      { name: '审核', actions: ['审核', '弃审', '审批情况'] },
      {
        name: '生单', actions: [
          '生成材料出库单', '生成材料出库单(分单)', '生成材料出库单(退料)',
          '生成工序汇报单（自制汇报）', '生成工序汇报单（委外汇报）',
          '生成工序汇报单（自制汇报退回）', '生成工序汇报单（委外汇报退回）',
          '生成产成品入库单', '生成产成品入库单(退库)',
          '生成补投生产加工单（按累计报废入库数量）', '生成返工生产加工单（按累计报废入库数量）',
        ],
      },
      { name: '变更', actions: ['变更'] },
      { name: '工具', actions: ['现存量查询', '变更历史'] },
      {
        name: '联查', actions: [
          '执行情况', '销售订单情况', '材料出库情况', '工序汇报情况', '产成品入库情况',
          '加工单执行图', '返工/补投加工情况', '生单流程联查',
        ],
      },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      {
        name: '打印', actions: [
          '直接打印', '打印', '预览', '打印模板设置', '导出', '明细标签打印', '工序流转卡打印', '打印情况',
        ],
      },
      {
        name: '更多', actions: [
          '复制', '放弃', '草稿', '中止执行', '取消中止', '重取BOM材料（仅追加）', '附件', '刷新', '消息',
        ],
      },
    ],
    version: '3.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '合同号', dataType: '日期', isRequired: true, defaultValue: today },
      { dataName: '锭号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '批号', dataType: '下拉框', isRequired: true, defaultValue: '正常', options: ['正常', '加急', '特急'] },
      { dataName: '生产车间', dataType: '下拉框', isRequired: true, defaultValue: '熔铸车间', options: WORKSHOP_OPTIONS },
      { dataName: '预开工日', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '预完工日', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '销售订单号', dataType: '文本', isRequired: false, defaultValue: '' },
      // 参照字段：弹窗拉取 往来单位 面板数据勾选导入（开发约束十一-1，能对应基础档案必须引用）
      { dataName: '客户编码', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位编码', displayField: '往来单位名称', filter: { 停用: false }, refMap: [{ from: '往来单位名称', to: '客户' }, { from: '往来单位名称', to: '生产订单客户' }], refColumns: ['往来单位编码', '往来单位名称', '结算客户', '分管部门', '分管人员', '停用'] },
      { dataName: '客户', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '测试程序', dataType: '下拉框', isRequired: true, defaultValue: '', options: ['光谱分析', '硬度测试', '拉伸测试', '金相检验', '超声波探伤'] },
      { dataName: '测试程序2', displayName: '测试程序', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '生产订单客户', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '机构', dataType: '下拉框', isRequired: true, defaultValue: '总部', options: ['总部', '华东分公司', '华南分公司'] },
      { dataName: '重量', dataType: '小数', isRequired: false, defaultValue: 0 },
      { dataName: '开工日期', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '完工日期', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '启用派工', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '自动转移', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '产品自动添加到材料', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '是否手工修改单据编码', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '外部单据号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '负责人', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['张工', '李工', '王工', '赵工', '陈工'] },
      { dataName: '启用领料申请', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '对方仓库', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['原料仓', '辅料仓', '成品仓', '半成品仓'] },
    ],
  },
  detail: {
    tabs: [
      {
        key: 'products', label: '产成品明细', isRequired: true,
        summaryItems: [
          { label: '数量合计', field: '数量' },
          { label: '齐套数量合计', field: '齐套数量(主)' },
          { label: '累计汇报套数合计', field: '累计汇报套数(工序单位)' },
          { label: '总重合计', field: '总重' },
        ],
        calc: [{ target: '总重', formula: '单重 * 数量', round: 2 }],
        fields: [
          { dataName: '生产类型', dataType: '下拉框', options: ['自制', '委外'], defaultValue: '自制' },
          { dataName: '产品编码', dataType: '下拉框', options: ['CP001', 'CP002', 'CP003', 'CP004', 'CP005'] },
          { dataName: '存货图片', dataType: '图片' },
          { dataName: '产品名称', dataType: '文本' },
          { dataName: '规格型号', dataType: '文本' },
          { dataName: '型号', dataType: '文本' },
          { dataName: '适用BOM', dataType: '下拉框', options: ['BOM-001', 'BOM-002', 'BOM-003'] },
          { dataName: 'BOM展开方式', dataType: '下拉框', options: ['单阶', '尾阶'], defaultValue: '单阶' },
          { dataName: '生产单位', dataType: '下拉框', options: ['件', 'kg', '套'], defaultValue: '件' },
          { dataName: '数量', dataType: '小数', defaultValue: 0 },
          { dataName: '齐套数量(主)', dataType: '小数', computed: true },
          { dataName: '累计汇报套数(工序单位)', dataType: '小数', computed: true },
          { dataName: '可用量', dataType: '小数', computed: true },
          { dataName: '可用量说明', dataType: '文本', computed: true },
          { dataName: '现存量', dataType: '小数', computed: true },
          { dataName: '现存量说明', dataType: '文本', computed: true },
          { dataName: '产品字符公用自定义项1', dataType: '文本' },
          { dataName: '图号', dataType: '文本' },
          { dataName: '单重', dataType: '小数', defaultValue: 0 },
          { dataName: '总重', dataType: '小数', computed: true },
          { dataName: '需求令号', dataType: '文本' },
        ],
      },
      {
        key: 'materials', label: '材料明细',
        summaryItems: [
          { label: '需用数量合计', field: '需用数量' },
          { label: '损耗数量合计', field: '损耗数量' },
          { label: '计划数量合计', field: '计划数量' },
          { label: '累计领用数量合计', field: '累计领用数量' },
          { label: '总重合计', field: '总重' },
        ],
        calc: [
          { target: '需用数量', formula: '定额需用数量 * 产品数量 / 定额生产数量', round: 2 },
          { target: '计划数量', formula: '需用数量 + 损耗数量', round: 2 },
          { target: '总重', formula: '单重 * 计划数量', round: 2 },
        ],
        fields: [
          // 参照字段：弹窗拉取 存货 面板数据勾选导入（filter 属性=外购 即材料类存货）
          { dataName: '材料编码', dataType: '参照', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { 属性: '外购' }, refMap: [{ from: '存货名称', to: '材料名称' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '计量单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '所属类别', '品牌', '计量单位', '停用'] },
          { dataName: '材料名称', dataType: '文本' },
          { dataName: '存货图片', dataType: '图片' },
          { dataName: '规格型号', dataType: '文本' },
          { dataName: '子件BOM', dataType: '文本' },
          { dataName: '预出仓库', dataType: '下拉框', options: ['原料仓', '辅料仓'], defaultValue: '原料仓' },
          { dataName: '材料倒冲方式', dataType: '下拉框', options: ['不倒冲', '倒冲', '按定额倒冲'], defaultValue: '不倒冲' },
          { dataName: '领料工序', dataType: '下拉框', options: PROCESS_OPTIONS },
          { dataName: '允许循环', dataType: '是否', defaultValue: false },
          { dataName: '行中止', dataType: '是否', defaultValue: false },
          { dataName: '计量单位', dataType: '下拉框', options: ['kg', '件', '套', '升'], defaultValue: 'kg' },
          { dataName: '定额生产数量', dataType: '小数', defaultValue: 1 },
          { dataName: '定额需用数量', dataType: '小数', defaultValue: 0 },
          { dataName: '定额需用数量2', dataType: '小数', defaultValue: 0 },
          { dataName: '需用数量', dataType: '小数', computed: true },
          { dataName: '损耗率%', dataType: '小数', defaultValue: 0, hidden: true },
          { dataName: '损耗数量', dataType: '小数', defaultValue: 0 },
          { dataName: '计划数量', dataType: '小数', computed: true },
          { dataName: '累计领用数量', dataType: '小数', computed: true },
          { dataName: '可用量', dataType: '小数', computed: true },
          { dataName: '可用量说明', dataType: '文本', computed: true },
          { dataName: '现存量', dataType: '小数', computed: true },
          { dataName: '现存量说明', dataType: '文本', computed: true },
          { dataName: '单重', dataType: '小数', defaultValue: 0 },
          { dataName: '总重', dataType: '小数', computed: true },
        ],
      },
      {
        key: 'processes', label: '工序明细',
        // 真实 T+ 工序明细没有「汇总」页签（仅产成品明细/材料明细有），故不配置 summaryItems
        calc: [
          { target: '金额', formula: '计划数量 * 工价', round: 2 },
          { target: '委外金额', formula: '委外单价 * 计划数量', round: 2 },
          { target: '总重', formula: '单重 * 计划数量', round: 2 },
        ],
        subTable: {
          label: '本工序材料',
          fields: [
            { dataName: '材料编码', dataType: '下拉框', options: ['CL001', 'CL002', 'CL003', 'CL004', 'CL005'] },
            { dataName: '材料名称', dataType: '文本' },
            { dataName: '规格型号', dataType: '文本' },
            { dataName: '计量单位', dataType: '下拉框', options: ['kg', '件', '套', '升'] },
            { dataName: '需用数量', dataType: '小数', defaultValue: 0 },
            { dataName: '损耗数量', dataType: '小数', defaultValue: 0 },
            { dataName: '计划数量', dataType: '小数', defaultValue: 0 },
          ],
        },
        fields: [
          { dataName: '工序行码', dataType: '文本', computed: true },
          { dataName: '工艺类型', dataType: '下拉框', options: ['自制', '委外'], defaultValue: '自制' },
          { dataName: '工艺序号', dataType: '整数', defaultValue: 0 },
          { dataName: '加工顺序', dataType: '整数', defaultValue: 1 },
          { dataName: '加工类型', dataType: '下拉框', options: ['自制', '委外'], defaultValue: '自制' },
          // 参照字段：弹窗拉取 工序 面板数据勾选导入（带出 工序名称/默认车间/备注）
          { dataName: '工序编码', dataType: '参照', refPanel: 'OP', refField: '工序编码', displayField: '工序名称', filter: { 是否停用: false }, refMap: [{ from: '工序名称', to: '工序名称' }, { from: '默认车间', to: '生产车间' }, { from: '备注', to: '工序备注' }], refColumns: ['工序编码', '工序名称', '默认车间', '加工方式', '标准合格率%', '备注', '是否停用'] },
          { dataName: '工序名称', dataType: '下拉框', options: PROCESS_OPTIONS },
          { dataName: '工序备注', dataType: '文本' },
          { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS },
          { dataName: '工作中心', dataType: '下拉框', options: ['WC-01 熔铸中心', 'WC-02 轧制中心', 'WC-03 机加中心', 'WC-04 检测中心'] },
          { dataName: '设备', dataType: '文本' },
          { dataName: '班组', dataType: '下拉框', options: ['下料班', '车工班', '铣工班', '热处理班', '质检班'] },
          { dataName: '工人', dataType: '文本' },
          { dataName: '委外供应商', dataType: '文本' },
          { dataName: '委外单价', dataType: '小数', defaultValue: 0 },
          { dataName: '税率%', dataType: '小数', defaultValue: 13 },
          { dataName: '委外金额', dataType: '小数', computed: true },
          { dataName: '按辅单位计价', dataType: '是否', defaultValue: false },
          { dataName: '计价辅单位', dataType: '下拉框', options: ['kg', '件'] },
          { dataName: '换算率(辅单位)', dataType: '小数', defaultValue: 1 },
          { dataName: '计价辅数量', dataType: '小数', defaultValue: 0 },
          { dataName: '工序完工状态', dataType: '下拉框', options: ['未开工', '进行中', '已完工'], defaultValue: '未开工' },
          { dataName: '手工完工', dataType: '是否', defaultValue: false },
          { dataName: '行中止', dataType: '是否', defaultValue: false },
          { dataName: '工价（辅单位）', dataType: '小数', defaultValue: 0 },
          { dataName: '工废工价', dataType: '小数', defaultValue: 0 },
          { dataName: '工废工价（辅单位）', dataType: '小数', defaultValue: 0 },
          { dataName: '料废工价', dataType: '小数', defaultValue: 0 },
          { dataName: '料废工价（辅单位）', dataType: '小数', defaultValue: 0 },
          { dataName: '工序单位', dataType: '下拉框', options: ['件', 'kg'], defaultValue: '件' },
          { dataName: '计划数量', dataType: '小数', defaultValue: 0 },
          { dataName: '工资类型', dataType: '下拉框', options: ['计件', '计时'], defaultValue: '计件' },
          { dataName: '工价', dataType: '小数', defaultValue: 0 },
          { dataName: '金额', dataType: '小数', computed: true },
          { dataName: '关键工序', dataType: '是否', defaultValue: false },
          { dataName: '单位标准工时', dataType: '小数', defaultValue: 0 },
          { dataName: '待返修数量-本序发现', dataType: '小数', computed: true },
          { dataName: '待返修数量-他序发现', dataType: '小数', computed: true },
          { dataName: '工序字符专用自定义项1', dataType: '文本' },
          { dataName: '计划时间', dataType: '文本' },
          { dataName: '完成时间', dataType: '文本' },
          { dataName: '单重', dataType: '小数', defaultValue: 0 },
          { dataName: '总重', dataType: '小数', computed: true },
          { dataName: '需求令号', dataType: '文本' },
        ],
      },
    ],
  },

  selectConfig: {
    source: 'SO_ORDER',
    title: '选销售订单（生单）',
    tip: '仅显示已审核且未中止的销售订单，选中的单据明细将带入生产加工单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '业务员', '预计交货日期', '存货名称', '数量', '销售单位'],
    headerMap: [
      { from: '单据编号', to: '销售订单号' },
      { from: '单据日期', to: '合同号' },
      { from: '客户', to: '客户' },
      { from: '客户编码', to: '客户编码' },
      { from: '结算客户', to: '生产订单客户' },
      { from: '业务员', to: '负责人' },
      { from: '预计交货日期', to: '预完工日' },
    ],
    detailMap: [
      { from: '存货编码', to: '产品编码' },
      { from: '存货名称', to: '产品名称' },
      { from: '规格型号', to: '规格型号' },
      { from: '销售单位', to: '生产单位' },
      { from: '数量', to: '数量' },
      { from: '现存量', to: '现存量' },
    ],
  },
}


// ==================== SO_ORDER 销售订单面板（2026-08-14 按实测 T+ SA03 一比一复刻） ====================
// 真实 T+：工具栏 新增/选单/保存/删除/审核/生单(生成生产加工单…)/变更/工具(联查)/设置/打印/更多(草稿 Alt+B·导入·整单中止)/智能
// 表头 2行5列：*单据编号/*单据日期/*客户/*结算客户/部门/部门.负责人/业务员/项目/预计交货日期/联系人
// 表体单明细 15 列（明细/汇总双子页签）；表尾 制单人/审核人/审核日期…

const SO_CONFIG = {
  metadata: {
    panelCode: 'SO_ORDER',
    panelName: '销售订单',
    panelCategory: '单据',
    autoCodeField: '单据编号', // 自动编码字段（锭号=MO-…，销售订单=SO-…）
    panelState: {
      dataName: '单据状态',
      dataType: 'STRING',
      defaultOptions: ['草稿', '已审核', '已中止', '已关闭'],
    },
    panelPageDto: {
      tablePages: [
        {
          tableName: '销售订单列表',
          queryFields: [
            { dataName: '单据编号', dataType: '文本' },
            { dataName: '单据日期', dataType: '日期' },
            { dataName: '客户', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
            { dataName: '业务员', dataType: '下拉框', options: ['张伟', '李娜', '王芳', '陈强'] },
            { dataName: '部门', dataType: '下拉框', options: ['销售一部', '销售二部', '国际部'] },
            { dataName: '预计交货日期', dataType: '日期' },
          ],
          gridTabs: [
            {
              label: '明细', rowSource: 'items',
              columns: ['存货名称.品牌', '存货名称', '存货编码', '规格型号', '数量', '销售单位', '单价', '税率%', '含税单价', '金额', '含税金额', '折扣金额', '预计交货日期', '现存量', '备注'],
            },
            {
              label: '汇总', rowSource: 'items', summary: true,
              columns: ['存货名称.品牌', '存货名称', '存货编码', '规格型号', '数量', '销售单位', '单价', '税率%', '含税单价', '金额', '含税金额', '折扣金额', '预计交货日期', '现存量', '备注'],
            },
          ],
          topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
          rowOperationBarBtn: [],
          events: [],
        },
      ],
      formPages: [
        {
          formName: '销售订单',
          fieldNames: '单据编号,单据日期,客户,客户编码,结算客户,部门,部门.负责人,业务员,项目,预计交货日期,联系人',
          bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '整单中止' }, { buttonName: '放弃' }],
          events: [],
        },
      ],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' },
      { buttonName: '整单中止' }, { buttonName: '草稿' }, { buttonName: '放弃' },
      { buttonName: '生成生产加工单' },
    ],
    // T+ 工具栏分组（2026-08-14 实测 SA03）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选单'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印'] },
      { name: '删除', actions: ['删除'] },
      { name: '审核', actions: ['审核', '弃审'] },
      {
        name: '生单', actions: [
          '生成销售出库单(普通销售)', '生成销售出库单(销售退货)', '生成生产加工单', '生成销售订单(销售退货)',
          '转成产成品入库单', '协同-生成对方销售订单',
        ],
      },
      { name: '变更', actions: ['变更'] },
      {
        name: '工具', actions: ['现存量查询', '变更历史', '联查', '出库情况', '生产加工情况', '订单执行图', '退货情况', '联查合同', '生单流程联查'],
      },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出', '明细标签打印', '打印情况'] },
      { name: '更多', actions: ['复制', '放弃', '草稿', '导入', '下载导入模板', '整单中止', '附件', '刷新', '消息'] },
      { name: '智能', actions: ['单据分享'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
      // 参照字段：弹窗拉取 往来单位 面板数据勾选导入（带出 客户编码/结算客户）
      { dataName: '客户', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false }, refMap: [{ from: '往来单位编码', to: '客户编码' }, { from: '往来单位名称', to: '结算客户' }], refColumns: ['往来单位编码', '往来单位名称', '结算客户', '分管部门', '分管人员', '停用'] },
      { dataName: '客户编码', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['KH001', 'KH002', 'KH003', 'KH004', 'KH005'] },
      { dataName: '结算客户', dataType: '下拉框', isRequired: true, defaultValue: '', options: ['华东铝业', '中天精工', '西部材料', '南方重工', '北方机械'] },
      { dataName: '部门', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['销售一部', '销售二部', '国际部'] },
      { dataName: '部门.负责人', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '业务员', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['张伟', '李娜', '王芳', '陈强'] },
      { dataName: '项目', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '预计交货日期', dataType: '日期', isRequired: false, defaultValue: '' },
      { dataName: '联系人', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [
      {
        key: 'items', label: '明细', isRequired: true,
        summaryItems: [
          { label: '数量合计', field: '数量' },
          { label: '金额合计', field: '金额' },
          { label: '含税金额合计', field: '含税金额' },
        ],
        calc: [
          { target: '含税单价', formula: '单价 * (1 + 税率% / 100)', round: 2 },
          { target: '金额', formula: '数量 * 单价', round: 2 },
          { target: '含税金额', formula: '数量 * 含税单价', round: 2 },
        ],
        fields: [
          { dataName: '存货名称.品牌', dataType: '文本' },
          // 参照字段：弹窗拉取 存货 面板数据勾选导入（带出 编码/规格/品牌/单位）
          { dataName: '存货名称', dataType: '参照', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', refMulti: true, filter: { 停用: false }, refMap: [{ from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' }, { from: '品牌', to: '存货名称.品牌' }, { from: '计量单位', to: '销售单位' }, { from: '参考成本', to: '单价' }], refColumns: ['存货编码', '存货名称', '规格型号', '品牌', '计量单位', '参考成本', '停用'] },
          { dataName: '存货编码', dataType: '下拉框', options: ['CP001', 'CP002', 'CP003', 'CP004', 'CP005'] },
          { dataName: '规格型号', dataType: '文本' },
          { dataName: '数量', dataType: '小数', defaultValue: 0 },
          { dataName: '销售单位', dataType: '下拉框', options: ['件', 'kg', '套'], defaultValue: '件' },
          { dataName: '单价', dataType: '小数', defaultValue: 0 },
          { dataName: '税率%', dataType: '小数', defaultValue: 13 },
          { dataName: '含税单价', dataType: '小数', computed: true },
          { dataName: '金额', dataType: '小数', computed: true },
          { dataName: '含税金额', dataType: '小数', computed: true },
          { dataName: '折扣金额', dataType: '小数', defaultValue: 0 },
          { dataName: '预计交货日期', dataType: '日期' },
          { dataName: '现存量', dataType: '小数', computed: true },
          { dataName: '备注', dataType: '文本' },
        ],
      },
    ],
  },
}

// ==================== 库存核算 6 单据面板（2026-08-14 按实测 T+ ST 系单据一比一复刻） ====================
// 真实 T+：BAPView/Voucher.aspx?sysId=ST&mId=ST1001|ST1002|ST1004|ST1021|ST1022|ST1024
// 共性：列表/表单同页；网格页签固定 明细+汇总（tab_RDRecordDetails / _Sum）
// 工具栏公共组：新增/保存/删除/审核/变更/设置/打印/更多；差异组见各面板 buttonGroups

const INVENTORY_OPTIONS = {
  warehouses: ['原料仓', '辅料仓', '成品仓', '半成品仓', '不良品仓'],
  inventory: ['铝棒 Φ80', '铝板 6061', '铝型材-散热片', '减速箱体 A', '轴套 C', '6061铝锭', '切削液', '包装木箱'],
  inventoryCode: ['CP001', 'CP002', 'CP003', 'CP004', 'CP005', 'CL001', 'CL002', 'CL004', 'CL005'],
  units: ['件', 'kg', '套', '升'],
  suppliers: ['华东铝业', '中天精工', '西部材料', '南方重工', '北方机械'],
  customers: ['华东铝业', '中天精工', '西部材料', '南方重工', '北方机械'],
  depts: ['销售一部', '销售二部', '国际部'],
  bizTypes: { IN: ['采购入库', '产成品入库', '其他入库'], OUT: ['销售出库', '材料出库', '其他出库'] },
  workshops: ['熔铸车间', '轧制车间', '精整车间', '测试车间'],
  persons: ['张伟', '李娜', '王芳', '陈强'],
  taxRate: [0, 3, 6, 9, 13],
}

// 库存单据公共工具栏组（T+ 实测公共部分）
function invCommonGroups(extraMore = []) {
  return [
    { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
    { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存为常用单据', '保存打印'] },
    { name: '删除', actions: ['删除'] },
    { name: '审核', actions: ['审核'] },
    { name: '变更', actions: ['变更'] },
    { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
    { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '导出', '明细标签打印', '打印情况'] },
    { name: '更多', actions: ['复制', '放弃', '草稿', '导入', '下载导入模板', ...extraMore, '附件', '刷新', '消息'] },
  ]
}

function invHeader(name, bizType) {
  return [
    { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
    { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
    { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: bizType, options: bizType === '采购入库' || bizType === '产成品入库' || bizType === '其他入库' ? INVENTORY_OPTIONS.bizTypes.IN : INVENTORY_OPTIONS.bizTypes.OUT },
    ...name,
  ]
}

// 面板工厂：metadata/dataSchema/detail 按各单据差异参数化
function invPanel({ panelCode, panelName, bizType, header, detailFields, detailLabel, summaryItems, calc, queryFields, gridColumns, sumColumns, toolbarDiff, extraMore = [], selectConfig }) {
  return {
    metadata: {
      panelCode,
      panelName,
      panelCategory: '单据',
      autoCodeField: '单据编号',
      panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '已中止', '已关闭'] },
      panelPageDto: {
        tablePages: [
          {
            tableName: panelName + '列表',
            queryFields,
            gridTabs: [
              { label: '明细', rowSource: 'detail', columns: gridColumns },
              { label: '汇总', rowSource: 'detail', summary: true, columns: sumColumns },
            ],
            topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
            rowOperationBarBtn: [],
            events: [],
          },
        ],
        formPages: [
          {
            formName: panelName,
            fieldNames: header.map((f) => f.dataName).join(','),
            bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
            events: [],
          },
        ],
      },
      panelButtons: [
        { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
        { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
      ],
      buttonGroups: [...invCommonGroups(extraMore), ...toolbarDiff],
      version: '1.0',
    },
    dataSchema: { type: 'object', fields: header },
    detail: {
      tabs: [
        {
          key: 'items', label: detailLabel, isRequired: true,
          summaryItems,
          calc,
          fields: detailFields,
        },
      ],
    },
    selectConfig,
  }
}

// ---------------- ① 采购入库单 ST1001（RK） ----------------
const PURCHASE_IN_CONFIG = invPanel({
  panelCode: 'PURCHASE_IN', panelName: '采购入库单', bizType: '采购入库', extraMore: ['重新取价'],
  header: invHeader([
    { dataName: '入库类别', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['采购入库', '其他入库'] },
    { dataName: '供应商编码', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['KH001', 'KH002', 'KH003', 'KH004', 'KH005'] },
    { dataName: '供应商', dataType: '下拉框', isRequired: true, defaultValue: '', options: INVENTORY_OPTIONS.suppliers },
    { dataName: '供应商简称', dataType: '文本', isRequired: true, defaultValue: '' },
    { dataName: '匹配来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '经手人', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.persons },
    { dataName: '验货人', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.persons },
    { dataName: '项目', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '仓库', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '来源单据', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '外部单据号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '销售订单号', dataType: '文本', isRequired: false, defaultValue: '' },
  ], '采购入库'),
  detailLabel: '明细',
  detailFields: [
    { dataName: '仓库', dataType: '下拉框', isRequired: true, defaultValue: '原料仓', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '存货名称', dataType: '下拉框', isRequired: true, defaultValue: '', options: INVENTORY_OPTIONS.inventory },
    { dataName: '存货图片', dataType: '图片' },
    { dataName: '规格型号', dataType: '文本' },
    { dataName: '实收数量', dataType: '小数', isRequired: true, defaultValue: 0 },
    { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: INVENTORY_OPTIONS.units },
    { dataName: '实收数量2', dataType: '小数', defaultValue: 0 },
    { dataName: '计量单位2', dataType: '下拉框', options: INVENTORY_OPTIONS.units },
    { dataName: '计量单位组合', dataType: '文本', computed: true },
    { dataName: '换算率', dataType: '小数', defaultValue: 1 },
    { dataName: '单价', dataType: '小数', defaultValue: 0 },
    { dataName: '税率%', dataType: '小数', defaultValue: 13, options: INVENTORY_OPTIONS.taxRate },
    { dataName: '单价2', dataType: '小数', defaultValue: 0 },
    { dataName: '含税单价2', dataType: '小数', computed: true },
    { dataName: '含税单价', dataType: '小数', computed: true },
    { dataName: '金额', dataType: '小数', computed: true },
    { dataName: '含税金额', dataType: '小数', computed: true },
    { dataName: '费用调整', dataType: '小数', defaultValue: 0 },
    { dataName: '费用金额', dataType: '小数', computed: true },
    { dataName: '现存量', dataType: '小数', computed: true },
    { dataName: '现存量说明', dataType: '文本', computed: true },
    { dataName: '产成品图片', dataType: '图片' },
  ],
  summaryItems: [
    { label: '实收数量合计', field: '实收数量' },
    { label: '金额合计', field: '金额' },
    { label: '含税金额合计', field: '含税金额' },
  ],
  calc: [
    { target: '含税单价', formula: '单价 * (1 + 税率% / 100)', round: 2 },
    { target: '含税单价2', formula: '单价2 * (1 + 税率% / 100)', round: 2 },
    { target: '金额', formula: '实收数量 * 单价', round: 2 },
    { target: '含税金额', formula: '实收数量 * 含税单价', round: 2 },
    { target: '费用金额', formula: '费用调整', round: 2 },
    { target: '计量单位组合', formula: '实收数量', round: 2 },
  ],
  queryFields: [
    { dataName: '单据日期', dataType: '日期' },
    { dataName: '单据编号', dataType: '文本' },
    { dataName: '业务类型', dataType: '下拉框', options: INVENTORY_OPTIONS.bizTypes.IN },
    { dataName: '入库类别', dataType: '下拉框', options: ['采购入库', '其他入库'] },
    { dataName: '供应商编码', dataType: '下拉框', options: ['KH001', 'KH002', 'KH003', 'KH004', 'KH005'] },
    { dataName: '供应商', dataType: '下拉框', options: INVENTORY_OPTIONS.suppliers },
    { dataName: '供应商简称', dataType: '文本' },
    { dataName: '匹配来源单号', dataType: '文本' },
    { dataName: '经手人', dataType: '下拉框', options: INVENTORY_OPTIONS.persons },
    { dataName: '验货人', dataType: '下拉框', options: INVENTORY_OPTIONS.persons },
    { dataName: '项目', dataType: '文本' },
    { dataName: '仓库', dataType: '下拉框', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '来源单据', dataType: '文本' },
    { dataName: '外部单据号', dataType: '文本' },
    { dataName: '来源单号', dataType: '文本' },
    { dataName: '销售订单号', dataType: '文本' },
  ],
  gridColumns: ['仓库', '存货名称', '存货图片', '规格型号', '实收数量', '计量单位', '实收数量2', '计量单位2', '计量单位组合', '换算率', '单价', '税率%', '单价2', '含税单价2', '含税单价', '金额', '含税金额', '费用调整', '费用金额', '现存量', '现存量说明', '产成品图片'],
  sumColumns: ['仓库', '存货名称', '存货图片', '规格型号', '计量单位', '实收数量', '单价', '金额', '含税单价', '含税金额'],
  toolbarDiff: [
    { name: '选单', actions: ['选单', '智能选单'] },
    { name: '转换', actions: ['转成材料出库单'] },
    { name: '协同', actions: ['协同'] },
    { name: '工具', actions: ['现存量查询', '变更历史', '联查', '入库调整情况', '生单流程联查'] },
  ],
})

// ---------------- ② 产成品入库单 ST1002（CP） ----------------
const FINISH_IN_CONFIG = invPanel({
  panelCode: 'FINISH_IN', panelName: '产成品入库单', bizType: '产成品入库',
  selectConfig: {
    source: 'MANU_ORDER',
    title: '选生产加工单',
    tip: '仅显示已审核且未中止的生产加工单，选中后产品明细带入产成品入库单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '预完工日', '产品名称', '数量', '生产单位'],
    headerMap: [
      { from: '单据编号', to: '加工单号' },
      { from: '单据编号', to: '匹配来源单号' },
    ],
    detailMap: [
      { from: '产品名称', to: '产品名称' },
      { from: '生产单位', to: '计量单位' },
      { from: '数量', to: '实收数量' },
      { from: '现存量', to: '现存量' },
    ],
  },

  header: invHeader([
    { dataName: '入库类别', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['自制加工入库', '退库入库'] },
    { dataName: '生产车间', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.workshops }, // 无车间档案面板，保留下拉（十一-1）
    { dataName: '加工单号', dataType: '文本', isRequired: false, defaultValue: '' },
    // 参照字段：能对应基础档案的字段弹窗拉取勾选导入（开发约束十一-1）
    { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
    { dataName: '验货人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
    { dataName: '验货日期', dataType: '日期', isRequired: false, defaultValue: '' },
    { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { 停用: false } },
    { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
    { dataName: '对方仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
    { dataName: '来源单据', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '外部单据号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '工序汇报单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '销售订单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '匹配来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
  ], '产成品入库'),
  detailLabel: '明细',
  detailFields: [
    // 参照字段：弹窗拉取 存货 面板数据勾选导入（带出 规格/单位/图号）
    { dataName: '产品名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { 停用: false }, refMap: [{ from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '计量单位' }, { from: '图号', to: '图号' }], refColumns: ['存货编码', '存货名称', '规格型号', '所属类别', '品牌', '计量单位', '停用'] },
    { dataName: '仓库', dataType: '参照', isRequired: true, defaultValue: '成品仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
    { dataName: '存货图片', dataType: '图片' },
    { dataName: '规格型号', dataType: '文本' },
    { dataName: '智能选单', dataType: '文本', computed: true },
    { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: INVENTORY_OPTIONS.units },
    { dataName: '金额', dataType: '小数', computed: true },
    { dataName: '单价', dataType: '小数', defaultValue: 0 },
    { dataName: '实收数量', dataType: '小数', isRequired: true, defaultValue: 0 },
    { dataName: '现存量', dataType: '小数', computed: true },
    { dataName: '现存量说明', dataType: '文本', computed: true },
    { dataName: '图号', dataType: '文本' },
  ],
  summaryItems: [
    { label: '实收数量合计', field: '实收数量' },
    { label: '金额合计', field: '金额' },
  ],
  calc: [{ target: '金额', formula: '实收数量 * 单价', round: 2 }],
  queryFields: [
    { dataName: '单据日期', dataType: '日期' },
    { dataName: '单据编号', dataType: '文本' },
    { dataName: '业务类型', dataType: '下拉框', options: INVENTORY_OPTIONS.bizTypes.IN },
    { dataName: '入库类别', dataType: '下拉框', options: ['自制加工入库', '退库入库'] },
    { dataName: '生产车间', dataType: '下拉框', options: INVENTORY_OPTIONS.workshops },
    { dataName: '加工单号', dataType: '文本' },
    { dataName: '仓库', dataType: '参照', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
    { dataName: '匹配来源单号', dataType: '文本' },
  ],
  gridColumns: ['产品名称', '仓库', '存货图片', '规格型号', '智能选单', '计量单位', '金额', '单价', '实收数量', '现存量', '现存量说明', '图号'],
  sumColumns: ['仓库', '产品名称', '规格型号', '计量单位', '实收数量', '单价', '金额'],
  toolbarDiff: [
    { name: '选单', actions: ['选单', '选产成品入库单（自制加工）', '选生产加工单'] },
    { name: '生单', actions: ['生成产成品入库单（自制退库）', '生成补投生产加工单', '生成返工生产加工单'] },
    { name: '转换', actions: ['转成销售出库单'] },
    { name: '工具', actions: ['现存量查询', '变更历史', '联查', '生产加工情况', '退库情况', '生单流程联查'] },
    { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置', '智能选单设置'] },
  ],
})

// ---------------- ③ 其他入库单 ST1004（IC） ----------------
const OTHER_IN_CONFIG = invPanel({
  panelCode: 'OTHER_IN', panelName: '其他入库单', bizType: '其他入库',
  header: invHeader([
    { dataName: '入库类别', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['盘盈入库', '调整入库', '其他'] },
    { dataName: '仓库', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '匹配来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '来料客户', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.customers },
  ], '其他入库'),
  detailLabel: '明细',
  detailFields: [
    { dataName: '仓库', dataType: '下拉框', isRequired: true, defaultValue: '原料仓', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '存货名称', dataType: '下拉框', isRequired: true, defaultValue: '', options: INVENTORY_OPTIONS.inventory },
    { dataName: '规格型号', dataType: '文本' },
    { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: INVENTORY_OPTIONS.units },
    { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
    { dataName: '智能选单', dataType: '文本', computed: true },
    { dataName: '计量单位2', dataType: '下拉框', options: INVENTORY_OPTIONS.units },
    { dataName: '数量2', dataType: '小数', defaultValue: 0 },
    { dataName: '单价', dataType: '小数', defaultValue: 0 },
    { dataName: '金额', dataType: '小数', computed: true },
    { dataName: '现存量', dataType: '小数', computed: true },
    { dataName: '现存量说明', dataType: '文本', computed: true },
  ],
  summaryItems: [
    { label: '数量合计', field: '数量' },
    { label: '金额合计', field: '金额' },
  ],
  calc: [{ target: '金额', formula: '数量 * 单价', round: 2 }],
  queryFields: [
    { dataName: '单据日期', dataType: '日期' },
    { dataName: '单据编号', dataType: '文本' },
    { dataName: '业务类型', dataType: '下拉框', options: INVENTORY_OPTIONS.bizTypes.IN },
    { dataName: '入库类别', dataType: '下拉框', options: ['盘盈入库', '调整入库', '其他'] },
    { dataName: '仓库', dataType: '下拉框', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '匹配来源单号', dataType: '文本' },
    { dataName: '来料客户', dataType: '下拉框', options: INVENTORY_OPTIONS.customers },
  ],
  gridColumns: ['仓库', '存货名称', '规格型号', '计量单位', '数量', '智能选单', '计量单位2', '数量2', '单价', '金额', '现存量', '现存量说明'],
  sumColumns: ['仓库', '存货名称', '规格型号', '计量单位', '数量', '单价', '金额'],
  toolbarDiff: [
    { name: '选单', actions: ['选单', '其他出库单'] },
    { name: '转换', actions: ['转换成其他出库单'] },
    { name: '工具', actions: ['现存量查询', '变更历史', '联查', '其他出库情况', '生单流程联查'] },
    { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置', '智能选单设置'] },
  ],
})

// ---------------- ④ 销售出库单 ST1021（IO） ----------------
const SALE_OUT_CONFIG = invPanel({
  panelCode: 'SALE_OUT', panelName: '销售出库单', bizType: '销售出库',
  selectConfig: {
    source: 'SO_ORDER',
    title: '选销售订单',
    tip: '仅显示已审核且未中止的销售订单，选中后明细带入销售出库单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '业务员', '预计交货日期', '存货名称', '数量', '销售单位'],
    headerMap: [
      { from: '客户', to: '客户' },
      { from: '结算客户', to: '结算客户' },
    ],
    detailMap: [
      { from: '存货名称', to: '存货名称' },
      { from: '存货编码', to: '存货编码' },
      { from: '规格型号', to: '规格型号' },
      { from: '销售单位', to: '计量单位' },
      { from: '数量', to: '数量' },
      { from: '现存量', to: '现存量' },
    ],
  },

  header: invHeader([
    { dataName: '退货原因', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['客户退货', '质量原因', '其他'] },
    { dataName: '出库类别', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['普通销售', '销售退货'] },
    // 参照字段：弹窗拉取 往来单位 面板数据勾选导入（带出 客户编码/结算客户/客户简称/部门）
    { dataName: '客户', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false }, refMap: [{ from: '往来单位编码', to: '客户编码' }, { from: '往来单位名称', to: '结算客户' }, { from: '往来单位简称', to: '客户简称' }, { from: '分管部门', to: '部门' }], refColumns: ['往来单位编码', '往来单位名称', '往来单位简称', '结算客户', '分管部门', '停用'] },
    { dataName: '客户编码', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位编码', displayField: '往来单位名称', filter: { 停用: false } },
    { dataName: '客户简称', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '结算客户', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
    { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { 停用: false } },
    { dataName: '门店', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '匹配来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
    { dataName: '验货人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
    { dataName: '验货日期', dataType: '日期', isRequired: false, defaultValue: '' },
    { dataName: '发货人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
    { dataName: '发货日期', dataType: '日期', isRequired: false, defaultValue: '' },
    { dataName: '项目', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '项目名称', displayField: '项目名称', filter: { 停用: false } },
    { dataName: '仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
    { dataName: '收货仓库', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
    { dataName: '来源单据', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '外部单据号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '销售订单号', dataType: '文本', isRequired: false, defaultValue: '' },
  ], '销售出库'),
  detailLabel: '明细',
  detailFields: [
    { dataName: '仓库', dataType: '参照', isRequired: true, defaultValue: '成品仓', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
    // 参照字段：弹窗拉取 存货 面板数据勾选导入（带出 编码/规格/单位）
    { dataName: '存货名称', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { 停用: false }, refMap: [{ from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '计量单位' }], refColumns: ['存货编码', '存货名称', '规格型号', '所属类别', '品牌', '计量单位', '停用'] },
    { dataName: '存货编码', dataType: '参照', isRequired: true, defaultValue: '', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { 停用: false } },
    { dataName: '规格型号', dataType: '文本' },
    { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: INVENTORY_OPTIONS.units },
    { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
    { dataName: '智能选单', dataType: '文本', computed: true },
    { dataName: '成本价', dataType: '小数', defaultValue: 0 },
    { dataName: '税率%', dataType: '小数', defaultValue: 13, options: INVENTORY_OPTIONS.taxRate },
    { dataName: '售价', dataType: '小数', defaultValue: 0 },
    { dataName: '含税售价', dataType: '小数', computed: true },
    { dataName: '销售金额', dataType: '小数', computed: true },
    { dataName: '税额', dataType: '小数', computed: true },
    { dataName: '含税销售金额', dataType: '小数', computed: true },
    { dataName: '折扣金额', dataType: '小数', defaultValue: 0 },
    { dataName: '现存量', dataType: '小数', computed: true },
    { dataName: '现存量说明', dataType: '文本', computed: true },
    { dataName: '需求令号', dataType: '文本' },
    { dataName: '退货原因', dataType: '下拉框', options: ['客户退货', '质量原因', '其他'] },
  ],
  summaryItems: [
    { label: '数量合计', field: '数量' },
    { label: '销售金额合计', field: '销售金额' },
    { label: '含税销售金额合计', field: '含税销售金额' },
  ],
  calc: [
    { target: '含税售价', formula: '售价 * (1 + 税率% / 100)', round: 2 },
    { target: '销售金额', formula: '数量 * 售价', round: 2 },
    { target: '税额', formula: '数量 * (含税售价 - 售价)', round: 2 },
    { target: '含税销售金额', formula: '销售金额 + 税额', round: 2 },
  ],
  queryFields: [
    { dataName: '单据日期', dataType: '日期' },
    { dataName: '单据编号', dataType: '文本' },
    { dataName: '业务类型', dataType: '下拉框', options: INVENTORY_OPTIONS.bizTypes.OUT },
    { dataName: '退货原因', dataType: '下拉框', options: ['客户退货', '质量原因', '其他'] },
    { dataName: '客户', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
    { dataName: '结算客户', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
    { dataName: '匹配来源单号', dataType: '文本' },
    { dataName: '经手人', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
    { dataName: '仓库', dataType: '参照', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
  ],
  gridColumns: ['仓库', '存货名称', '存货编码', '规格型号', '计量单位', '数量', '智能选单', '成本价', '税率%', '售价', '含税售价', '销售金额', '税额', '含税销售金额', '折扣金额', '现存量', '现存量说明', '需求令号', '退货原因'],
  sumColumns: ['仓库', '存货名称', '规格型号', '计量单位', '数量', '成本价', '售价', '销售金额', '含税销售金额'],
  toolbarDiff: [
    { name: '选单', actions: ['选单', '选销售订单'] },
    { name: '生单', actions: ['生成销售出库单(销售退货)'] },
    { name: '工具', actions: ['现存量查询', '变更历史', '联查', '销售订单情况', '退库情况', '出库情况', '生单流程联查'] },
    { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置', '智能选单设置'] },
  ],
})

// ---------------- ⑤ 材料出库单 ST1022（MD） ----------------
const MATERIAL_OUT_CONFIG = invPanel({
  panelCode: 'MATERIAL_OUT', panelName: '材料出库单', bizType: '材料出库',
  selectConfig: {
    source: 'MANU_ORDER',
    title: '选生产加工单',
    tip: '仅显示已审核且未中止的生产加工单，选中后材料明细带入材料出库单（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '预完工日', '产品名称', '数量', '生产单位'],
    headerMap: [
      { from: '单据编号', to: '来源单据' },
      { from: '销售订单号', to: '销售订单号' },
    ],
    detailMap: [
      { from: '产品名称', to: '材料名称' },
      { from: '生产单位', to: '计量单位' },
      { from: '数量', to: '数量' },
      { from: '现存量', to: '现存量' },
    ],
  },

  header: invHeader([
    { dataName: '出库类别', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['直接领料', '自制领料', '退料'] },
    { dataName: '生产车间', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.workshops },
    { dataName: '领用人', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.persons },
    { dataName: '仓库', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '来源单据', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '销售订单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '匹配来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
  ], '材料出库'),
  detailLabel: '明细',
  detailFields: [
    { dataName: '仓库', dataType: '下拉框', isRequired: true, defaultValue: '原料仓', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '加工单号', dataType: '文本', isRequired: false, defaultValue: '' },
    { dataName: '材料名称', dataType: '下拉框', isRequired: true, defaultValue: '', options: INVENTORY_OPTIONS.inventory },
    { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: 'kg', options: INVENTORY_OPTIONS.units },
    { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
    { dataName: '单价', dataType: '小数', defaultValue: 0 },
    { dataName: '金额', dataType: '小数', computed: true },
    { dataName: '规格型号', dataType: '文本' },
    { dataName: '手工确定成本', dataType: '是否', defaultValue: false },
    { dataName: '明细备注', dataType: '文本' },
    { dataName: '现存量', dataType: '小数', computed: true },
    { dataName: '现存量说明', dataType: '文本', computed: true },
  ],
  summaryItems: [
    { label: '数量合计', field: '数量' },
    { label: '金额合计', field: '金额' },
  ],
  calc: [{ target: '金额', formula: '数量 * 单价', round: 2 }],
  queryFields: [
    { dataName: '单据日期', dataType: '日期' },
    { dataName: '单据编号', dataType: '文本' },
    { dataName: '业务类型', dataType: '下拉框', options: INVENTORY_OPTIONS.bizTypes.OUT },
    { dataName: '出库类别', dataType: '下拉框', options: ['直接领料', '自制领料', '退料'] },
    { dataName: '生产车间', dataType: '下拉框', options: INVENTORY_OPTIONS.workshops },
    { dataName: '领用人', dataType: '下拉框', options: INVENTORY_OPTIONS.persons },
    { dataName: '仓库', dataType: '下拉框', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '来源单据', dataType: '文本' },
    { dataName: '销售订单号', dataType: '文本' },
    { dataName: '匹配来源单号', dataType: '文本' },
  ],
  gridColumns: ['仓库', '加工单号', '材料名称', '计量单位', '数量', '单价', '金额', '规格型号', '手工确定成本', '明细备注', '现存量', '现存量说明'],
  sumColumns: ['仓库', '加工单号', '材料名称', '规格型号', '计量单位', '数量', '单价', '金额'],
  toolbarDiff: [
    { name: '选单', actions: ['选单', '选材料出库单（直接领料）', '选材料出库单（自制领料）', '选生产加工单', '选生产加工单(新增材料)'] },
    { name: '选单转换', actions: ['选单转换', '选材料出库单（自制领料）', '选生产加工单'] },
    { name: '生单', actions: ['生成材料出库单（直接退料）', '生成材料出库单（自制退料）'] },
    { name: '工具', actions: ['现存量查询', '变更历史', '联查', '生产加工情况', '退料情况', '生单流程联查'] },
  ],
})

// ---------------- ⑥ 其他出库单 ST1024（ID） ----------------
const OTHER_OUT_CONFIG = invPanel({
  panelCode: 'OTHER_OUT', panelName: '其他出库单', bizType: '其他出库',
  header: invHeader([
    { dataName: '仓库', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '来料客户', dataType: '下拉框', isRequired: false, defaultValue: '', options: INVENTORY_OPTIONS.customers },
  ], '其他出库'),
  detailLabel: '明细',
  detailFields: [
    { dataName: '仓库', dataType: '下拉框', isRequired: true, defaultValue: '原料仓', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '存货名称', dataType: '下拉框', isRequired: true, defaultValue: '', options: INVENTORY_OPTIONS.inventory },
    { dataName: '规格型号', dataType: '文本' },
    { dataName: '计量单位', dataType: '下拉框', isRequired: true, defaultValue: '件', options: INVENTORY_OPTIONS.units },
    { dataName: '数量', dataType: '小数', isRequired: true, defaultValue: 0 },
    { dataName: '单价', dataType: '小数', defaultValue: 0 },
    { dataName: '金额', dataType: '小数', computed: true },
    { dataName: '现存量', dataType: '小数', computed: true },
    { dataName: '现存量说明', dataType: '文本', computed: true },
  ],
  summaryItems: [
    { label: '数量合计', field: '数量' },
    { label: '金额合计', field: '金额' },
  ],
  calc: [{ target: '金额', formula: '数量 * 单价', round: 2 }],
  queryFields: [
    { dataName: '单据日期', dataType: '日期' },
    { dataName: '单据编号', dataType: '文本' },
    { dataName: '业务类型', dataType: '下拉框', options: INVENTORY_OPTIONS.bizTypes.OUT },
    { dataName: '仓库', dataType: '下拉框', options: INVENTORY_OPTIONS.warehouses },
    { dataName: '来料客户', dataType: '下拉框', options: INVENTORY_OPTIONS.customers },
  ],
  gridColumns: ['仓库', '存货名称', '规格型号', '计量单位', '数量', '单价', '金额', '现存量', '现存量说明'],
  sumColumns: ['仓库', '存货名称', '规格型号', '计量单位', '数量', '单价', '金额'],
  toolbarDiff: [
    { name: '转换', actions: ['转换成其他入库单'] },
    { name: '工具', actions: ['现存量查询', '变更历史', '联查', '其他入库情况', '联查设备投放单', '生单流程联查'] },
  ],
})

// 工序汇报单网格列（对齐真实 MR10 明细/汇总）
const PROCESS_REPORT_GRID_COLUMNS = ['加工单号', '产品编码', '产品名称', '规格型号', '计划时间', '完成时间', '工艺类型', '工艺序号', '加工顺序', '工艺路线', '工序编码', '工序名称', '工序备注', '车间编码', '生产车间', '计件依据', '班组名称', '工人名称', '班组成员/多工人', '设备', '报工数量', '合格数量', '不合格数量', '工资类型', '工作中心', '委外供应商', '工序单位', '委外部门', '委外业务员', '累计汇报数量', '可报工数量', '合格率%', '按辅单位计价', '计价辅单位', '检验返工数量', '返工原因', '跨序待修转出数量', '返修责任工序', '责任来源汇报明细', '返修转出目的工序', '工废数量', '工废原因', '料废数量', '料废原因', '工价', '报废预入仓库', '单位标准工时', '开工日期', '开工时间', '完工日期', '完工时间', '手工完工', '实际工时', '计时/计件金额', '委外单价', '税率%', '委外含税单价', '委外金额', '委外税额', '委外含税金额', '调整工资', '金额', '备注', '需求令号', '图号', '领倒冲料']
const PROCESS_REPORT_SUM_COLUMNS = ['产品编码', '产品名称', '工序单位', '工序编码', '工序名称', '报工数量', '合格数量', '不合格数量', '工价', '计时/计件金额', '委外单价', '委外含税单价', '委外金额', '委外税额', '委外含税金额']

// ---------------- ⑦ 工序汇报单 MR10（MR）2026-08-14 实测 T+ 一比一复刻 ----------------
// 表头/明细列/工具栏/生单按 docs/design/T+销售出库单-产成品入库单-工序汇报单-真实面板设计.md
const PROCESS_REPORT_CONFIG = {
  metadata: {
    panelCode: 'PROCESS_REPORT',
    panelName: '工序汇报单',
    panelCategory: '单据',
    autoCodeField: '单据编号',
    panelState: { dataName: '单据状态', dataType: 'STRING', defaultOptions: ['草稿', '已审核', '生产中', '已完工', '已中止', '已关闭'] },
    panelPageDto: {
      tablePages: [
        {
          tableName: '工序汇报单列表',
          queryFields: [
            { dataName: '单据日期', dataType: '日期' },
            { dataName: '单据编号', dataType: '文本' },
            { dataName: '业务类型', dataType: '下拉框', options: ['工序汇报', '返修汇报'] },
            { dataName: '部门', dataType: '参照', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { 停用: false } },
            { dataName: '加工单号', dataType: '文本' },
            { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS },
            { dataName: '产品编码', dataType: '参照', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { 停用: false } },
            { dataName: '产品名称', dataType: '参照', refPanel: 'INV', refField: '存货名称', displayField: '存货名称', filter: { 停用: false } },
            { dataName: '客户', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
            { dataName: '测试程序', dataType: '下拉框', options: ['光谱分析', '硬度测试', '拉伸测试', '金相检验', '超声波探伤'] },
            { dataName: '匹配来源单号', dataType: '文本' },
          ],
          gridTabs: [
            { label: '明细', rowSource: 'items', columns: PROCESS_REPORT_GRID_COLUMNS },
            { label: '汇总', rowSource: 'items', summary: true, columns: PROCESS_REPORT_SUM_COLUMNS },
          ],
          topBarBtn: [{ buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' }],
          rowOperationBarBtn: [],
          events: [],
        },
      ],
      formPages: [
        {
          formName: '工序汇报单',
          fieldNames: '单据日期,单据编号,业务类型,部门,经手人,加工单号,生产车间,委外供应商,产品编码,产品名称,规格型号,销售订单号,客户,末工序按合格数量自动入库,是否手动改价,检验工序自动报检,报废自动入库,保持汇报数量不变,返修汇报,返修模式,生单模式,待补录,测试程序,匹配来源单号',
          bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '删除' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' }],
          events: [],
        },
      ],
    },
    panelButtons: [
      { buttonName: '新增流程' }, { buttonName: '删除' }, { buttonName: '刷新' },
      { buttonName: '保存' }, { buttonName: '审核' }, { buttonName: '弃审' }, { buttonName: '放弃' },
    ],
    // T+ 工具栏分组（2026-08-14 实测 MR10）
    buttonGroups: [
      { name: '新增', actions: ['新增', '引入常用单据', '设置默认功能'] },
      { name: '选单', actions: ['选单', '选生产加工单', '智能选单'] },
      { name: '修改', actions: ['修改'] },
      { name: '保存', actions: ['保存', '保存新增', '保存为草稿', '保存打印'] },
      { name: '删除', actions: ['删除'] },
      { name: '审核', actions: ['审核', '弃审', '审批情况'] },
      { name: '生单', actions: ['生成产成品入库单', '生成产成品入库单（废品）', '生成补投生产加工单', '生成返工生产加工单'] },
      { name: '变更', actions: ['变更'] },
      { name: '工具', actions: ['变更历史', '联查', '生产加工情况', '材料出库情况', '产成品入库情况', '生单流程联查'] },
      { name: '设置', actions: ['单据设置', '移动控件位置', '调整控件宽度', '工具栏设置'] },
      { name: '打印', actions: ['直接打印', '打印', '预览', '打印模板设置', '明细标签打印', '导出'] },
      { name: '更多', actions: ['工序条码录入', '放弃', '草稿', '附件', '刷新', '消息'] },
    ],
    version: '1.0',
  },
  dataSchema: {
    type: 'object',
    fields: [
      { dataName: '单据日期', dataType: '日期', isRequired: true, defaultValue: today },
      { dataName: '单据编号', dataType: '文本', isRequired: true, defaultValue: '', autoCode: true },
      { dataName: '业务类型', dataType: '下拉框', isRequired: true, defaultValue: '工序汇报', options: ['工序汇报', '返修汇报'] },
      { dataName: '部门', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'DEPT', refField: '部门名称', displayField: '部门名称', filter: { 停用: false } },
      { dataName: '经手人', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
      { dataName: '加工单号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '生产车间', dataType: '下拉框', isRequired: false, defaultValue: '', options: WORKSHOP_OPTIONS }, // 无车间档案面板，保留下拉（十一-1）
      { dataName: '委外供应商', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
      // 参照字段：弹窗拉取 存货 面板数据勾选导入（带出 产品名称/规格型号）
      { dataName: '产品编码', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { 停用: false }, refMap: [{ from: '存货名称', to: '产品名称' }, { from: '规格型号', to: '规格型号' }], refColumns: ['存货编码', '存货名称', '规格型号', '所属类别', '品牌', '计量单位', '停用'] },
      { dataName: '产品名称', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '规格型号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '销售订单号', dataType: '文本', isRequired: false, defaultValue: '' },
      { dataName: '客户', dataType: '参照', isRequired: false, defaultValue: '', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
      { dataName: '末工序按合格数量自动入库', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '是否手动改价', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '检验工序自动报检', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '报废自动入库', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '保持汇报数量不变', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '返修汇报', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '返修模式', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['按原工序返修', '指定工序返修'] },
      { dataName: '生单模式', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['按工序汇报生单', '按合格数量生单'] },
      { dataName: '待补录', dataType: '是否', isRequired: false, defaultValue: false },
      { dataName: '测试程序', dataType: '下拉框', isRequired: false, defaultValue: '', options: ['光谱分析', '硬度测试', '拉伸测试', '金相检验', '超声波探伤'] },
      { dataName: '匹配来源单号', dataType: '文本', isRequired: false, defaultValue: '' },
    ],
  },
  detail: {
    tabs: [
      {
        key: 'items', label: '明细', isRequired: true,
        summaryItems: [
          { label: '报工数量合计', field: '报工数量' },
          { label: '合格数量合计', field: '合格数量' },
          { label: '不合格数量合计', field: '不合格数量' },
          { label: '工废数量合计', field: '工废数量' },
          { label: '料废数量合计', field: '料废数量' },
          { label: '金额合计', field: '金额' },
        ],
        calc: [
          { target: '计时/计件金额', formula: '报工数量 * 工价', round: 2 },
          { target: '金额', formula: '计时/计件金额 + 调整工资', round: 2 },
          { target: '合格率%', formula: '合格数量 / 报工数量 * 100', round: 2 },
          { target: '委外含税单价', formula: '委外单价 * (1 + 税率% / 100)', round: 2 },
          { target: '委外金额', formula: '报工数量 * 委外单价', round: 2 },
          { target: '委外税额', formula: '报工数量 * (委外含税单价 - 委外单价)', round: 2 },
          { target: '委外含税金额', formula: '委外金额 + 委外税额', round: 2 },
        ],
        subTable: {
          label: '班组成员',
          fields: [
            { dataName: '工人编码', dataType: '参照', refPanel: 'EMP', refField: '员工编码', displayField: '员工名称', filter: { 停用: false }, refMap: [{ from: '员工名称', to: '工人名称' }] },
            { dataName: '工人名称', dataType: '文本' },
            { dataName: '能力权重系数', dataType: '小数', defaultValue: 1 },
            { dataName: '工时权重系数', dataType: '小数', defaultValue: 1 },
            { dataName: '计件分配比例%', dataType: '小数', defaultValue: 0 },
            { dataName: '报工数量', dataType: '小数', defaultValue: 0 },
            { dataName: '合格数量', dataType: '小数', defaultValue: 0 },
            { dataName: '不合格数量', dataType: '小数', defaultValue: 0 },
            { dataName: '工废数量', dataType: '小数', defaultValue: 0 },
            { dataName: '料废数量', dataType: '小数', defaultValue: 0 },
            { dataName: '实际工时', dataType: '小数', defaultValue: 0 },
            { dataName: '调整工资', dataType: '小数', defaultValue: 0 },
            { dataName: '备注', dataType: '文本' },
          ],
        },
        fields: [
          { dataName: '加工单号', dataType: '文本' },
          // 参照字段：弹窗拉取 存货 面板数据勾选导入（带出 名称/规格/单位/图号）
          { dataName: '产品编码', dataType: '参照', refPanel: 'INV', refField: '存货编码', displayField: '存货名称', filter: { 停用: false }, refMap: [{ from: '存货名称', to: '产品名称' }, { from: '规格型号', to: '规格型号' }, { from: '计量单位', to: '工序单位' }, { from: '图号', to: '图号' }], refColumns: ['存货编码', '存货名称', '规格型号', '品牌', '计量单位', '停用'] },
          { dataName: '产品名称', dataType: '文本' },
          { dataName: '规格型号', dataType: '文本' },
          { dataName: '计划时间', dataType: '文本' },
          { dataName: '完成时间', dataType: '文本' },
          { dataName: '工艺类型', dataType: '下拉框', options: ['自制', '委外'], defaultValue: '自制' },
          { dataName: '工艺序号', dataType: '整数', defaultValue: 0 },
          { dataName: '加工顺序', dataType: '整数', defaultValue: 1 },
          { dataName: '工艺路线', dataType: '文本' },
          // 参照字段：弹窗拉取 工序 面板数据勾选导入（带出 名称/车间/备注/工资类型/计件依据）
          { dataName: '工序编码', dataType: '参照', isRequired: true, refPanel: 'OP', refField: '工序编码', displayField: '工序名称', filter: { 是否停用: false }, refMap: [{ from: '工序名称', to: '工序名称' }, { from: '默认车间', to: '生产车间' }, { from: '备注', to: '工序备注' }, { from: '默认工资类型', to: '工资类型' }, { from: '计件依据', to: '计件依据' }], refColumns: ['工序编码', '工序名称', '默认车间', '加工方式', '标准合格率%', '备注', '是否停用'] },
          { dataName: '工序名称', dataType: '文本' },
          { dataName: '工序备注', dataType: '文本' },
          { dataName: '车间编码', dataType: '文本' },
          { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS },
          { dataName: '计件依据', dataType: '下拉框', options: ['报工数量', '合格数量', '计件产量'], defaultValue: '报工数量' },
          { dataName: '班组名称', dataType: '参照', refPanel: 'TEAM', refField: '班组名称', displayField: '班组名称', filter: { 是否停用: false } },
          { dataName: '工人名称', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
          { dataName: '班组成员/多工人', dataType: '文本' },
          { dataName: '设备', dataType: '参照', refPanel: 'EQUIP', refField: '设备名称', displayField: '设备名称', filter: { 停用: false } },
          { dataName: '报工数量', dataType: '小数', isRequired: true, defaultValue: 0 },
          { dataName: '合格数量', dataType: '小数', defaultValue: 0 },
          { dataName: '不合格数量', dataType: '小数', defaultValue: 0 },
          { dataName: '工资类型', dataType: '下拉框', options: ['计件', '计时'], defaultValue: '计件' },
          { dataName: '工作中心', dataType: '参照', refPanel: 'WC', refField: '工作中心名称', displayField: '工作中心名称', filter: { 停用: false } },
          { dataName: '委外供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称', filter: { 停用: false } },
          { dataName: '工序单位', dataType: '下拉框', options: ['件', 'kg', '套'], defaultValue: '件' },
          { dataName: '委外部门', dataType: '文本' },
          { dataName: '委外业务员', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称', filter: { 停用: false } },
          { dataName: '累计汇报数量', dataType: '小数', computed: true },
          { dataName: '可报工数量', dataType: '小数', computed: true },
          { dataName: '合格率%', dataType: '小数', computed: true },
          { dataName: '按辅单位计价', dataType: '是否', defaultValue: false },
          { dataName: '计价辅单位', dataType: '下拉框', options: ['件', 'kg'] },
          { dataName: '检验返工数量', dataType: '小数', defaultValue: 0 },
          { dataName: '返工原因', dataType: '下拉框', options: ['尺寸超差', '外观缺陷', '性能不合格', '其他'] },
          { dataName: '跨序待修转出数量', dataType: '小数', defaultValue: 0 },
          { dataName: '返修责任工序', dataType: '文本' },
          { dataName: '责任来源汇报明细', dataType: '文本' },
          { dataName: '返修转出目的工序', dataType: '文本' },
          { dataName: '工废数量', dataType: '小数', defaultValue: 0 },
          { dataName: '工废原因', dataType: '下拉框', options: ['操作失误', '设备故障', '材料缺陷', '其他'] },
          { dataName: '料废数量', dataType: '小数', defaultValue: 0 },
          { dataName: '料废原因', dataType: '下拉框', options: ['材料缺陷', '来料不良', '其他'] },
          { dataName: '工价', dataType: '小数', defaultValue: 0 },
          { dataName: '报废预入仓库', dataType: '参照', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称', filter: { 停用: false } },
          { dataName: '单位标准工时', dataType: '小数', defaultValue: 0 },
          { dataName: '开工日期', dataType: '日期' },
          { dataName: '开工时间', dataType: '文本' },
          { dataName: '完工日期', dataType: '日期' },
          { dataName: '完工时间', dataType: '文本' },
          { dataName: '手工完工', dataType: '是否', defaultValue: false },
          { dataName: '实际工时', dataType: '小数', defaultValue: 0 },
          { dataName: '计时/计件金额', dataType: '小数', computed: true },
          { dataName: '委外单价', dataType: '小数', defaultValue: 0 },
          { dataName: '税率%', dataType: '小数', defaultValue: 13, options: INVENTORY_OPTIONS.taxRate },
          { dataName: '委外含税单价', dataType: '小数', computed: true },
          { dataName: '委外金额', dataType: '小数', computed: true },
          { dataName: '委外税额', dataType: '小数', computed: true },
          { dataName: '委外含税金额', dataType: '小数', computed: true },
          { dataName: '调整工资', dataType: '小数', defaultValue: 0 },
          { dataName: '金额', dataType: '小数', computed: true },
          { dataName: '备注', dataType: '文本' },
          { dataName: '需求令号', dataType: '文本' },
          { dataName: '图号', dataType: '文本' },
          { dataName: '领倒冲料', dataType: '是否', defaultValue: false },
        ],
      },
    ],
  },
  selectConfig: {
    source: 'MANU_ORDER',
    title: '选生产加工单',
    tip: '仅显示已审核且未中止的生产加工单，选中后按工序明细带入待汇报行（对齐 T+ 选单前提）',
    columns: ['单据编号', '单据日期', '客户', '预完工日', '产品名称', '数量', '生产单位'],
    headerMap: [
      { from: '单据编号', to: '加工单号' },
      { from: '单据编号', to: '匹配来源单号' },
      { from: '客户', to: '客户' },
      { from: '销售订单号', to: '销售订单号' },
    ],
    detailMap: [
      { from: '工序编码', to: '工序编码' },
      { from: '工序名称', to: '工序名称' },
      { from: '工序备注', to: '工序备注' },
      { from: '生产车间', to: '生产车间' },
      { from: '工作中心', to: '工作中心' },
      { from: '设备', to: '设备' },
      { from: '产品编码', to: '产品编码' },
      { from: '产品名称', to: '产品名称' },
      { from: '规格型号', to: '规格型号' },
      { from: '工序单位', to: '工序单位' },
      { from: '计划数量', to: '报工数量' },
      { from: '工价', to: '工价' },
      { from: '工艺类型', to: '工艺类型' },
      { from: '工艺序号', to: '工艺序号' },
      { from: '加工顺序', to: '加工顺序' },
      { from: '委外供应商', to: '委外供应商' },
      { from: '班组', to: '班组名称' },
      { from: '工人', to: '工人名称' },
      { from: '需求令号', to: '需求令号' },
      { from: '图号', to: '图号' },
      { from: '单位标准工时', to: '单位标准工时' },
    ],
    // 选单带出工序明细行（对齐 T+：选生产加工单 → 待汇报工序行）
    detailRows: (row) => {
      const r = MOCK_ROWS.find((x) => x['编号'] === (row['编号'] || row['单据编号']))
      const prods = (r && r.detail && r.detail.products) || []
      const head = prods[0] || {}
      // 工序行本身不含产品信息，从单据头/产成品明细行补齐（对齐 T+ 选单带出）
      return ((r && r.detail && r.detail.processes) || []).map((p) => ({
        加工单号: r['锭号'] || r['单据编号'] || '',
        产品编码: p['产品编码'] ?? head['产品编码'] ?? '',
        产品名称: p['产品名称'] ?? head['产品名称'] ?? '',
        规格型号: p['规格型号'] ?? head['规格型号'] ?? '',
        ...p,
      }))
    },
  },
}

// 工序汇报单演示数据（表头 + items 工序汇报行，联动 MANU 工序/存货/工序 档案数据）
const PROCESS_REPORT_ROWS = [
  {
    编号: 'MR-2026-08-0001', 单据编号: 'MR-2026-08-0001', 单据日期: today, 业务类型: '工序汇报', 单据状态: '已审核', 审批状态: '已审批',
    部门: '生产部', 经手人: '张伟', 加工单号: 'MO-2026-08-0001', 生产车间: '熔铸车间', 产品编码: 'CP001', 产品名称: '铝棒 Φ80',
    规格型号: 'Φ80×3000', 销售订单号: 'SO-2026-08-0001', 客户: '华东铝业', 末工序按合格数量自动入库: false,
    测试程序: '光谱分析', 匹配来源单号: 'MO-2026-08-0001',
    制单人: 'tplusdemo12862', 审核人: 'tplusdemo12862', 审核日期: today, 审核时间: '09:32:00', 打印次数: 0,
    创建时间: '2026-08-14 09:30:00', 更新时间: '2026-08-14 09:32:00',
    detail: {
      items: [
        { 加工单号: 'MO-2026-08-0001', 产品编码: 'CP001', 产品名称: '铝棒 Φ80', 规格型号: 'Φ80×3000', 工艺类型: '自制', 工艺序号: 1, 加工顺序: 1, 工序编码: 'PX001', 工序名称: '下料', 生产车间: '熔铸车间', 班组名称: '下料班', 工人名称: '张伟', 报工数量: 200, 合格数量: 198, 不合格数量: 2, 工资类型: '计件', 工序单位: '件', 工价: 5, '计时/计件金额': 990, 金额: 990, 图号: 'T-001', 需求令号: 'REQ-01', 子表材料: [] },
        { 加工单号: 'MO-2026-08-0001', 产品编码: 'CP001', 产品名称: '铝棒 Φ80', 规格型号: 'Φ80×3000', 工艺类型: '自制', 工艺序号: 2, 加工顺序: 2, 工序编码: 'PX002', 工序名称: '车削', 生产车间: '熔铸车间', 班组名称: '车工班', 工人名称: '李娜', 报工数量: 180, 合格数量: 180, 不合格数量: 0, 工资类型: '计件', 工序单位: '件', 工价: 8, '计时/计件金额': 1440, 金额: 1440, 图号: 'T-001', 需求令号: 'REQ-01', 子表材料: [] },
      ],
    },
  },
  {
    编号: 'MR-2026-08-0002', 单据编号: 'MR-2026-08-0002', 单据日期: today, 业务类型: '工序汇报', 单据状态: '草稿',
    部门: '生产部', 经手人: '王芳', 加工单号: 'MO-2026-08-0002', 生产车间: '精整车间', 产品编码: 'CP003', 产品名称: '铝型材-散热片',
    规格型号: 'XD-6063-T5', 销售订单号: 'SO-2026-08-0003', 客户: '西部材料', 末工序按合格数量自动入库: false,
    制单人: 'tplusdemo12862', 打印次数: 0, 创建时间: '2026-08-14 10:05:00', 更新时间: '2026-08-14 10:05:00',
    detail: {
      items: [
        { 加工单号: 'MO-2026-08-0002', 产品编码: 'CP003', 产品名称: '铝型材-散热片', 规格型号: 'XD-6063-T5', 工艺类型: '自制', 工艺序号: 1, 加工顺序: 1, 工序编码: 'PX003', 工序名称: '铣削', 生产车间: '精整车间', 班组名称: '铣工班', 工人名称: '陈强', 报工数量: 300, 合格数量: 296, 不合格数量: 4, 工资类型: '计件', 工序单位: '件', 工价: 6, '计时/计件金额': 1776, 金额: 1776, 需求令号: 'REQ-03', 子表材料: [] },
      ],
    },
  },
]

// 工序汇报单列表展平（明细行）
function flattenProcessReportRows() {
  const out = []
  for (const r of PROCESS_REPORT_ROWS) {
    const { detail, ...head } = r
    for (const it of detail?.items || []) out.push({ ...head, ...it })
  }
  out.sort((a, b) => (a['编号'] < b['编号'] ? 1 : -1))
  return out
}

const INV_CONFIGS = {
  PURCHASE_IN: PURCHASE_IN_CONFIG,
  FINISH_IN: FINISH_IN_CONFIG,
  OTHER_IN: OTHER_IN_CONFIG,
  SALE_OUT: SALE_OUT_CONFIG,
  MATERIAL_OUT: MATERIAL_OUT_CONFIG,
  OTHER_OUT: OTHER_OUT_CONFIG,

  selectConfig: {
    source: 'SO_ORDER',
    title: '选销售订单',
    tip: '销售订单选单（对齐 T+ 选单前提：已审核且未中止）',
    columns: ['单据编号', '单据日期', '客户', '业务员', '预计交货日期', '存货名称', '数量', '销售单位'],
    headerMap: [],
    detailMap: [],
  },
}


// ---------------- 库存核算 6 单据演示数据（对齐真实 T+ 库存单据结构与编号规则） ----------------

const I = (o) => ({ 仓库: '原料仓', 存货名称: '', 存货图片: '', 规格型号: '', 计量单位: '件', 数量: 0, 单价: 0, 金额: 0, 现存量: 0, 现存量说明: '', ...o })

const INV_SEED = {
  PURCHASE_IN: [
    {
      编号: 'RK-2026-08-0001', 单据状态: '已审核', 审批状态: '已审批', 创建时间: today + ' 08:20', 更新时间: today + ' 08:30', 发起人编号: 'tplusdemo12853',
      单据日期: '2026-08-12', 单据编号: 'RK-2026-08-0001', 业务类型: '采购入库', 入库类别: '采购入库',
      供应商编码: 'KH001', 供应商: '华东铝业', 供应商简称: '华东铝业', 匹配来源单号: 'PO-2026-08-0001',
      经手人: '张伟', 验货人: '李娜', 项目: '铝棒采购', 仓库: '原料仓', 来源单据: '采购订单', 外部单据号: 'EXT-PO-01', 来源单号: 'PO-2026-08-0001', 销售订单号: 'SO-2026-08-0001',
      detail: { items: [
        I({ 仓库: '原料仓', 存货名称: '铝棒 Φ80', 规格型号: 'Φ80×3000', 实收数量: 200, 计量单位: '件', 换算率: 1, 单价: 15.5, '税率%': 13, 含税单价: 17.52, 金额: 3100, 含税金额: 3503, 现存量: 800, 现存量说明: '充足' }),
        I({ 仓库: '原料仓', 存货名称: '6061铝锭', 规格型号: 'A00', 实收数量: 500, 计量单位: 'kg', 换算率: 1, 单价: 12.8, '税率%': 13, 含税单价: 14.46, 金额: 6400, 含税金额: 7232, 现存量: 6000, 现存量说明: '充足' }),
      ] },
    },
    {
      编号: 'RK-2026-08-0002', 单据状态: '草稿', 创建时间: today + ' 10:00', 更新时间: today + ' 10:00', 发起人编号: 'tplusdemo12853',
      单据日期: today, 单据编号: 'RK-2026-08-0002', 业务类型: '采购入库', 入库类别: '采购入库',
      供应商编码: 'KH002', 供应商: '中天精工', 供应商简称: '中天精工', 匹配来源单号: '',
      经手人: '李娜', 验货人: '', 项目: '', 仓库: '原料仓', 来源单据: '', 外部单据号: '', 来源单号: '', 销售订单号: '',
      detail: { items: [] },
    },
  ],
  FINISH_IN: [
    {
      编号: 'CP-2026-08-0001', 单据状态: '已审核', 创建时间: today + ' 09:00', 更新时间: today + ' 09:10', 发起人编号: 'tplusdemo12853',
      单据日期: today, 单据编号: 'CP-2026-08-0001', 业务类型: '产成品入库', 入库类别: '自制加工入库',
      生产车间: '熔铸车间', 加工单号: 'MO-2026-08-0009', 仓库: '成品仓', 匹配来源单号: 'MO-2026-08-0009',
      detail: { items: [
        I({ 仓库: '成品仓', 产品名称: '铝棒 Φ80', 规格型号: 'Φ80×3000', 计量单位: '件', 实收数量: 200, 单价: 15.5, 金额: 3100, 现存量: 800, 现存量说明: '充足', 图号: 'T-001' }),
        I({ 仓库: '成品仓', 产品名称: '铝板 6061', 规格型号: '1500×3000×2', 计量单位: '件', 实收数量: 100, 单价: 12.8, 金额: 1280, 现存量: 450, 现存量说明: '充足', 图号: 'T-002' }),
      ] },
    },
    {
      编号: 'CP-2026-08-0002', 单据状态: '草稿', 创建时间: today + ' 10:30', 更新时间: today + ' 10:30', 发起人编号: 'tplusdemo12853',
      单据日期: today, 单据编号: 'CP-2026-08-0002', 业务类型: '产成品入库', 入库类别: '自制加工入库',
      生产车间: '轧制车间', 加工单号: 'MO-2026-08-0008', 仓库: '成品仓', 匹配来源单号: '',
      detail: { items: [] },
    },
  ],
  OTHER_IN: [
    {
      编号: 'IC-2026-08-0001', 单据状态: '已审核', 创建时间: today + ' 08:00', 更新时间: today + ' 08:05', 发起人编号: 'tplusdemo12853',
      单据日期: '2026-08-11', 单据编号: 'IC-2026-08-0001', 业务类型: '其他入库', 入库类别: '盘盈入库',
      仓库: '原料仓', 匹配来源单号: '', 来料客户: '西部材料',
      detail: { items: [
        I({ 仓库: '原料仓', 存货名称: '包装木箱', 规格型号: '1200×800', 计量单位: '件', 数量: 30, 单价: 5, 金额: 150, 现存量: 300, 现存量说明: '充足' }),
      ] },
    },
  ],
  SALE_OUT: [
    {
      编号: 'IO-2026-08-0011', 单据状态: '已审核', 审批状态: '已审批', 创建时间: today + ' 08:30', 更新时间: today + ' 08:35', 发起人编号: 'tplusdemo12853',
      单据日期: today, 单据编号: 'IO-2026-08-0011', 业务类型: '销售出库', 退货原因: '',
      客户: '华东铝业', 结算客户: '华东铝业', 匹配来源单号: 'SO-2026-08-0001', 经手人: '张伟', 仓库: '成品仓',
      detail: { items: [
        I({ 仓库: '成品仓', 存货名称: '铝棒 Φ80', 存货编码: 'CP001', 规格型号: 'Φ80×3000', 计量单位: '件', 数量: 200, 成本价: 10.2, '税率%': 13, 售价: 15.5, 含税售价: 17.52, 销售金额: 3100, 税额: 403, 含税销售金额: 3503, 现存量: 800, 现存量说明: '充足', 需求令号: 'REQ-01', 退货原因: '' }),
        I({ 仓库: '成品仓', 存货名称: '铝板 6061', 存货编码: 'CP002', 规格型号: '1500×3000×2', 计量单位: '件', 数量: 100, 成本价: 8.6, '税率%': 13, 售价: 12.8, 含税售价: 14.46, 销售金额: 1280, 税额: 166.4, 含税销售金额: 1446.4, 现存量: 450, 现存量说明: '充足', 需求令号: 'REQ-01', 退货原因: '' }),
      ] },
    },
  ],
  MATERIAL_OUT: [
    {
      编号: 'MD-2026-08-0002', 单据状态: '已审核', 创建时间: today + ' 09:20', 更新时间: today + ' 09:25', 发起人编号: 'tplusdemo12853',
      单据日期: today, 单据编号: 'MD-2026-08-0002', 业务类型: '材料出库', 出库类别: '自制领料',
      生产车间: '熔铸车间', 领用人: '王强', 仓库: '原料仓', 来源单据: '生产加工单', 销售订单号: 'SO-2026-08-0001', 匹配来源单号: 'MO-2026-08-0009',
      detail: { items: [
        I({ 仓库: '原料仓', 加工单号: 'MO-2026-08-0009', 材料名称: '6061铝锭', 计量单位: 'kg', 数量: 315, 单价: 12.8, 金额: 4032, 规格型号: 'A00', 手工确定成本: false, 明细备注: '下料领用', 现存量: 6000, 现存量说明: '充足' }),
        I({ 仓库: '辅料仓', 加工单号: 'MO-2026-08-0009', 材料名称: '切削液', 计量单位: '升', 数量: 6, 单价: 18, 金额: 108, 规格型号: '20L/桶', 手工确定成本: false, 明细备注: '', 现存量: 260, 现存量说明: '充足' }),
      ] },
    },
  ],
  OTHER_OUT: [
    {
      编号: 'ID-2026-08-0001', 单据状态: '已审核', 创建时间: today + ' 07:50', 更新时间: today + ' 07:55', 发起人编号: 'tplusdemo12853',
      单据日期: '2026-08-13', 单据编号: 'ID-2026-08-0001', 业务类型: '其他出库',
      仓库: '不良品仓', 来料客户: '',
      detail: { items: [
        I({ 仓库: '不良品仓', 存货名称: '铝板 6061', 规格型号: '1500×3000×2', 计量单位: '件', 数量: 5, 单价: 12.8, 金额: 64, 现存量: 450, 现存量说明: '充足' }),
      ] },
    },
  ],
}

// 库存单据自动单号：RK/CP/IC/IO/MD/ID-yyyy-MM-####（对齐 T+ 库存单据编码规则实测）
const INV_PREFIX = { PURCHASE_IN: 'RK', FINISH_IN: 'CP', OTHER_IN: 'IC', SALE_OUT: 'IO', MATERIAL_OUT: 'MD', OTHER_OUT: 'ID' }

function nextInvNo(panelCode) {
  const prefix = `${INV_PREFIX[panelCode]}-${today}-`
  const rows = INV_SEED[panelCode] || []
  const nums = rows.map((r) => r['单据编号'] || '').filter((s) => s.startsWith(prefix)).map((s) => Number(s.slice(prefix.length))).filter((n) => !Number.isNaN(n))
  return prefix + String((nums.length ? Math.max(...nums) : 0) + 1).padStart(4, '0')
}

// 库存列表展平：明细行（对齐 T+ 库存单据列表按明细行展示）
function flattenInvRows(panelCode) {
  const out = []
  for (const r of INV_SEED[panelCode] || []) {
    const { detail, ...head } = r
    for (const it of detail?.items || []) {
      out.push({ ...head, ...it, 子表数量: 1 })
    }
  }
  out.sort((a, b) => (a['编号'] < b['编号'] ? 1 : -1))
  return out
}

// ---------------- mock 演示数据（3 单 7 行产成品明细，对齐真实演示账套） ----------------

const P = (o) => ({ 生产类型: '自制', 产品编码: 'CP001', 存货图片: '', 产品名称: '', 规格型号: '', 型号: '', 适用BOM: 'BOM-001', 'BOM展开方式': '单阶', 生产单位: '件', 数量: 0, '齐套数量(主)': 0, '累计汇报套数(工序单位)': 0, 可用量: 0, 可用量说明: '', 现存量: 0, 现存量说明: '', '产品字符公用自定义项1': '', 图号: '', 单重: 0, 总重: 0, 需求令号: '', ...o })
const M = (o) => ({ 材料编码: 'CL001', 材料名称: '', 存货图片: '', 规格型号: '', 子件BOM: '', 预出仓库: '原料仓', 材料倒冲方式: '不倒冲', 领料工序: '', 允许循环: false, 行中止: false, 计量单位: 'kg', 定额生产数量: 1, 定额需用数量: 0, 定额需用数量2: 0, 需用数量: 0, '损耗率%': 0, 损耗数量: 0, 计划数量: 0, 累计领用数量: 0, 可用量: 0, 可用量说明: '', 现存量: 0, 现存量说明: '', 单重: 0, 总重: 0, ...o })
const C = (o) => ({ 工序行码: '', 工艺类型: '自制', 工艺序号: 0, 加工顺序: 1, 加工类型: '自制', 工序编码: 'PX001', 工序名称: '下料', 工序备注: '', 生产车间: '熔铸车间', 工作中心: '', 设备: '', 班组: '', 工人: '', 委外供应商: '', 委外单价: 0, '税率%': 13, 委外金额: 0, 按辅单位计价: false, 计价辅单位: '件', '换算率(辅单位)': 1, 计价辅数量: 0, 工序完工状态: '未开工', 手工完工: false, 行中止: false, '工价（辅单位）': 0, 工废工价: 0, '工废工价（辅单位）': 0, 料废工价: 0, '料废工价（辅单位）': 0, 工序单位: '件', 计划数量: 0, 工资类型: '计件', 工价: 0, 金额: 0, 关键工序: false, 单位标准工时: 0, '待返修数量-本序发现': 0, '待返修数量-他序发现': 0, '工序字符专用自定义项1': '', 计划时间: '', 完成时间: '', 单重: 0, 总重: 0, 需求令号: '', 子表材料: [], ...o })

let MOCK_ROWS = [
  {
    编号: 'MO-2026-08-0009', 单据状态: '生产中', 审批状态: '已审批', 创建时间: today + ' 09:00', 更新时间: today + ' 09:30', 发起人编号: 'tplusdemo12855',
    审核人: '系统管理员', 审核日期: today, 审核时间: today + ' 09:05', 变更人: '', 变更日期: '', 审核机器人: '',
    合同号: today, 锭号: 'MO-2026-08-0009', 批号: '正常', 生产车间: '熔铸车间', 预开工日: today, 预完工日: '2026-08-20',
    销售订单号: 'SO-2026-08-0001', 客户编码: 'KH001', 客户: '华东铝业', 测试程序: '光谱分析', 生产订单客户: '华东铝业',
    机构: '总部', 重量: 5.2, 开工日期: today, 完工日期: '', 启用派工: true, 自动转移: true,
    产品自动添加到材料: false, 是否手工修改单据编码: false, 外部单据号: 'EXT-9001', 负责人: '张工', 启用领料申请: false, 对方仓库: '成品仓',
    detail: {
      products: [
        P({ 产品编码: 'CP001', 产品名称: '铝棒 Φ80', 规格型号: 'Φ80×3000', 型号: 'AL-B80', 数量: 200, '齐套数量(主)': 200, '累计汇报套数(工序单位)': 160, 可用量: 500, 可用量说明: '可用', 现存量: 800, 现存量说明: '充足', 图号: 'T-001', 单重: 8.2, 总重: 1640, 需求令号: 'REQ-01' }),
        P({ 产品编码: 'CP002', 产品名称: '铝板 6061', 规格型号: '1500×3000×2', 型号: 'AL-P6061', 数量: 100, '齐套数量(主)': 100, '累计汇报套数(工序单位)': 0, 可用量: 300, 可用量说明: '可用', 现存量: 450, 现存量说明: '充足', 图号: 'T-002', 单重: 3.1, 总重: 310, 需求令号: 'REQ-01' }),
      ],
      materials: [
        M({ 材料编码: 'CL002', 材料名称: '6061铝锭', 规格型号: 'A00', 预出仓库: '原料仓', 材料倒冲方式: '按定额倒冲', 领料工序: '下料', 计量单位: 'kg', 定额生产数量: 1, 定额需用数量: 1.05, 需用数量: 315, '损耗率%': 5, 损耗数量: 15.75, 计划数量: 330.75, 累计领用数量: 300, 可用量: 5000, 现存量: 6000, 单重: 1, 总重: 330.75 }),
        M({ 材料编码: 'CL004', 材料名称: '切削液', 规格型号: '20L/桶', 预出仓库: '辅料仓', 领料工序: '车削', 计量单位: '升', 定额生产数量: 100, 定额需用数量: 2, 需用数量: 6, '损耗率%': 2, 损耗数量: 0.12, 计划数量: 6.12, 累计领用数量: 5.5, 可用量: 200, 现存量: 260, 单重: 0.9, 总重: 5.51 }),
      ],
      processes: [
        C({ 加工顺序: 1, 工序编码: 'PX001', 工序名称: '下料', 生产车间: '熔铸车间', 工作中心: 'WC-01 熔铸中心', 设备: '锯床-01', 班组: '下料班', 工人: '王强', 工序完工状态: '已完工', 手工完工: true, 工序单位: '件', 计划数量: 200, 工价: 2.5, 金额: 500, 关键工序: false, 单位标准工时: 0.05, 计划时间: today + ' 08:00', 完成时间: today + ' 12:00', 需求令号: 'REQ-01', 子表材料: [{ 材料编码: 'CL002', 材料名称: '6061铝锭', 规格型号: 'A00', 计量单位: 'kg', 需用数量: 210, 损耗数量: 10.5, 计划数量: 220.5 }] }),
        C({ 加工顺序: 2, 工序编码: 'PX002', 工序名称: '车削', 生产车间: '熔铸车间', 工作中心: 'WC-03 机加中心', 设备: '数控车床-03', 班组: '车工班', 工人: '李丽', 工序完工状态: '进行中', 工序单位: '件', 计划数量: 200, 工价: 5.8, 金额: 1160, 关键工序: true, 单位标准工时: 0.12, '待返修数量-本序发现': 2, '待返修数量-他序发现': 1, 计划时间: today + ' 13:00', 完成时间: '', 需求令号: 'REQ-01', 子表材料: [] }),
        C({ 加工顺序: 3, 工序编码: 'PX007', 工序名称: '检验', 生产车间: '熔铸车间', 工作中心: 'WC-04 检测中心', 设备: '检测台-01', 班组: '质检班', 工人: '赵刚', 工序完工状态: '未开工', 工序单位: '件', 计划数量: 200, 工价: 1.2, 金额: 240, 关键工序: false, 单位标准工时: 0.03, 计划时间: '', 完成时间: '', 需求令号: 'REQ-01', 子表材料: [] }),
      ],
    },
  },
  {
    编号: 'MO-2026-08-0008', 单据状态: '已完工', 审批状态: '已审批', 创建时间: today + ' 08:30', 更新时间: today + ' 08:40', 发起人编号: 'tplusdemo12855',
    审核人: '系统管理员', 审核日期: '2026-08-12', 审核时间: '2026-08-12 09:00', 变更人: '李工', 变更日期: '2026-08-15', 审核机器人: '',
    合同号: '2026-08-12', 锭号: 'MO-2026-08-0008', 批号: '加急', 生产车间: '轧制车间', 预开工日: '2026-08-12', 预完工日: '2026-08-15',
    销售订单号: 'SO-2026-08-0002', 客户编码: 'KH002', 客户: '中天精工', 测试程序: '硬度测试', 生产订单客户: '中天精工',
    机构: '总部', 重量: 8.6, 开工日期: '2026-08-12', 完工日期: '2026-08-15', 启用派工: false, 自动转移: false,
    产品自动添加到材料: false, 是否手工修改单据编码: false, 外部单据号: '', 负责人: '李工', 启用领料申请: true, 对方仓库: '成品仓',
    detail: {
      products: [
        P({ 产品编码: 'CP002', 产品名称: '铝板 6061', 规格型号: '1500×3000×2', 型号: 'AL-P6061', 数量: 500, '齐套数量(主)': 500, '累计汇报套数(工序单位)': 500, 可用量: 300, 可用量说明: '可用', 现存量: 450, 现存量说明: '充足', 图号: 'T-002', 单重: 3.1, 总重: 1550, 需求令号: 'REQ-02' }),
        P({ 产品编码: 'CP004', 产品名称: '减速箱体 A', 规格型号: 'JS-400', 型号: 'BOX-A', 数量: 50, '齐套数量(主)': 50, '累计汇报套数(工序单位)': 50, 可用量: 80, 可用量说明: '可用', 现存量: 120, 现存量说明: '充足', 图号: 'T-004', 单重: 42, 总重: 2100, 需求令号: 'REQ-02' }),
        P({ 产品编码: 'CP005', 产品名称: '轴套 C', 规格型号: 'ZT-C-30', 型号: 'SLEEVE-C', 数量: 800, '齐套数量(主)': 800, '累计汇报套数(工序单位)': 800, 可用量: 1000, 可用量说明: '可用', 现存量: 1600, 现存量说明: '充足', 图号: 'T-005', 单重: 0.6, 总重: 480, 需求令号: 'REQ-02' }),
      ],
      materials: [
        M({ 材料编码: 'CL002', 材料名称: '6061铝锭', 规格型号: 'A00', 预出仓库: '原料仓', 材料倒冲方式: '按定额倒冲', 领料工序: '下料', 计量单位: 'kg', 定额生产数量: 1, 定额需用数量: 1.08, 需用数量: 540, '损耗率%': 8, 损耗数量: 43.2, 计划数量: 583.2, 累计领用数量: 583.2, 可用量: 5000, 现存量: 6000, 单重: 1, 总重: 583.2 }),
        M({ 材料编码: 'CL001', 材料名称: '45#圆钢', 规格型号: 'Φ60', 预出仓库: '原料仓', 领料工序: '车削', 计量单位: 'kg', 定额生产数量: 1, 定额需用数量: 1.02, 需用数量: 51, '损耗率%': 2, 损耗数量: 1.02, 计划数量: 52.02, 累计领用数量: 52.02, 可用量: 800, 现存量: 900, 单重: 1, 总重: 52.02 }),
        M({ 材料编码: 'CL005', 材料名称: '包装木箱', 规格型号: '1200×800', 预出仓库: '辅料仓', 领料工序: '检验', 计量单位: '件', 定额生产数量: 50, 定额需用数量: 1, 需用数量: 27, '损耗率%': 0, 损耗数量: 0, 计划数量: 27, 累计领用数量: 27, 可用量: 200, 现存量: 300, 单重: 5, 总重: 135 }),
      ],
      processes: [
        C({ 加工顺序: 1, 工序编码: 'PX001', 工序名称: '下料', 生产车间: '轧制车间', 工作中心: 'WC-02 轧制中心', 设备: '锯床-02', 班组: '下料班', 工人: '王强', 工序完工状态: '已完工', 手工完工: true, 工序单位: '件', 计划数量: 1350, 工价: 1.8, 金额: 2430, 关键工序: false, 单位标准工时: 0.02, 计划时间: '2026-08-12 08:00', 完成时间: '2026-08-13 18:00', 需求令号: 'REQ-02', 子表材料: [{ 材料编码: 'CL002', 材料名称: '6061铝锭', 规格型号: 'A00', 计量单位: 'kg', 需用数量: 540, 损耗数量: 43.2, 计划数量: 583.2 }] }),
        C({ 加工顺序: 2, 加工类型: '委外', 工艺类型: '委外', 工序编码: 'PX005', 工序名称: '热处理', 生产车间: '轧制车间', 工作中心: 'WC-02 轧制中心', 设备: '', 班组: '热处理班', 工人: '', 委外供应商: '华东热处理厂', 委外单价: 1.5, 委外金额: 2025, 工序完工状态: '已完工', 手工完工: true, 工序单位: '件', 计划数量: 1350, 工资类型: '计时', 工价: 0, 金额: 0, 关键工序: true, 单位标准工时: 0.5, '待返修数量-他序发现': 2, 计划时间: '2026-08-13 08:00', 完成时间: '2026-08-14 10:00', 需求令号: 'REQ-02', 子表材料: [] }),
        C({ 加工顺序: 3, 工序编码: 'PX007', 工序名称: '检验', 生产车间: '轧制车间', 工作中心: 'WC-04 检测中心', 设备: '检测台-02', 班组: '质检班', 工人: '赵刚', 工序完工状态: '已完工', 手工完工: true, 工序单位: '件', 计划数量: 1350, 工价: 1, 金额: 1350, 关键工序: false, 单位标准工时: 0.01, 计划时间: '2026-08-14 10:00', 完成时间: '2026-08-15 09:00', 需求令号: 'REQ-02', 子表材料: [] }),
      ],
    },
  },
  {
    编号: 'MO-2026-08-0007', 单据状态: '已审核', 创建时间: today + ' 10:00', 更新时间: today + ' 10:10', 发起人编号: 'tplusdemo12855',
    审核人: '系统管理员', 审核日期: '2026-08-11', 审核时间: '2026-08-11 10:05', 变更人: '', 变更日期: '', 审核机器人: '',
    合同号: '2026-08-11', 锭号: 'MO-2026-08-0007', 批号: '正常', 生产车间: '精整车间', 预开工日: '2026-08-14', 预完工日: '2026-08-22',
    销售订单号: 'SO-2026-08-0004', 客户编码: 'KH003', 客户: '西部材料', 测试程序: '金相检验', 生产订单客户: '西部材料',
    机构: '华东分公司', 重量: 3.4, 开工日期: '', 完工日期: '', 启用派工: true, 自动转移: true,
    产品自动添加到材料: true, 是否手工修改单据编码: false, 外部单据号: '', 负责人: '王工', 启用领料申请: false, 对方仓库: '成品仓',
    detail: {
      products: [
        P({ 产品编码: 'CP003', 产品名称: '铝型材-散热片', 规格型号: 'XD-6063-T5', 型号: 'FIN-6063', 数量: 300, '齐套数量(主)': 300, '累计汇报套数(工序单位)': 60, 可用量: 400, 可用量说明: '可用', 现存量: 600, 现存量说明: '充足', 图号: 'T-003', 单重: 1.4, 总重: 420, 需求令号: 'REQ-03' }),
        P({ 产品编码: 'CP004', 产品名称: '减速箱体 A', 规格型号: 'JS-400', 型号: 'BOX-A', 数量: 40, '齐套数量(主)': 40, '累计汇报套数(工序单位)': 0, 可用量: 80, 可用量说明: '可用', 现存量: 120, 现存量说明: '充足', 图号: 'T-004', 单重: 42, 总重: 1680, 需求令号: 'REQ-03' }),
      ],
      materials: [
        M({ 材料编码: 'CL002', 材料名称: '6063铝棒', 规格型号: 'Φ120', 预出仓库: '原料仓', 领料工序: '下料', 计量单位: 'kg', 定额生产数量: 1, 定额需用数量: 1.1, 需用数量: 374, '损耗率%': 10, 损耗数量: 37.4, 计划数量: 411.4, 累计领用数量: 0, 可用量: 3000, 现存量: 4200, 单重: 1, 总重: 411.4 }),
        M({ 材料编码: 'CL005', 材料名称: '包装木箱', 规格型号: '1200×800', 预出仓库: '辅料仓', 领料工序: '检验', 计量单位: '件', 定额生产数量: 50, 定额需用数量: 1, 需用数量: 6.8, '损耗率%': 0, 损耗数量: 0, 计划数量: 6.8, 累计领用数量: 0, 可用量: 200, 现存量: 300, 单重: 5, 总重: 34 }),
      ],
      processes: [
        C({ 加工顺序: 1, 工序编码: 'PX001', 工序名称: '下料', 生产车间: '精整车间', 工作中心: 'WC-03 机加中心', 设备: '锯床-01', 班组: '下料班', 工人: '王强', 工序完工状态: '进行中', 工序单位: '件', 计划数量: 340, 工价: 2.2, 金额: 748, 关键工序: false, 单位标准工时: 0.04, 计划时间: '2026-08-14 08:00', 完成时间: '', 需求令号: 'REQ-03', 子表材料: [{ 材料编码: 'CL002', 材料名称: '6063铝棒', 规格型号: 'Φ120', 计量单位: 'kg', 需用数量: 374, 损耗数量: 37.4, 计划数量: 411.4 }] }),
        C({ 加工顺序: 2, 工序编码: 'PX003', 工序名称: '铣削', 生产车间: '精整车间', 工作中心: 'WC-03 机加中心', 设备: '加工中心-02', 班组: '铣工班', 工人: '孙涛', 工序完工状态: '未开工', 工序单位: '件', 计划数量: 340, 工价: 6.5, 金额: 2210, 关键工序: true, 单位标准工时: 0.15, '待返修数量-本序发现': 3, 计划时间: '', 完成时间: '', 需求令号: 'REQ-03', 子表材料: [] }),
        C({ 加工顺序: 3, 工序编码: 'PX007', 工序名称: '检验', 生产车间: '精整车间', 工作中心: 'WC-04 检测中心', 设备: '检测台-01', 班组: '质检班', 工人: '赵刚', 工序完工状态: '未开工', 工序单位: '件', 计划数量: 340, 工价: 1.2, 金额: 408, 关键工序: false, 单位标准工时: 0.02, 计划时间: '', 完成时间: '', 需求令号: 'REQ-03', 子表材料: [] }),
      ],
    },
  },
  {
    编号: 'MO-2026-08-0010', 单据状态: '已审核', 创建时间: today + ' 09:00', 更新时间: today + ' 09:05', 发起人编号: 'tplusdemo12855',
    审核人: '系统管理员', 审核日期: '2026-08-12', 审核时间: '2026-08-12 09:05', 变更人: '', 变更日期: '', 审核机器人: '',
    合同号: '2026-08-12', 锭号: 'MO-2026-08-0010', 批号: '加急', 生产车间: '熔铸车间', 预开工日: '2026-08-15', 预完工日: '2026-08-25',
    销售订单号: 'SO-2026-08-0002', 客户编码: 'KH002', 客户: '中天精工', 测试程序: '硬度测试', 生产订单客户: '中天精工',
    机构: '总部', 重量: 2.1, 开工日期: '', 完工日期: '', 启用派工: true, 自动转移: true,
    产品自动添加到材料: true, 是否手工修改单据编码: false, 外部单据号: '', 负责人: '李工', 启用领料申请: false, 对方仓库: '成品仓',
    detail: {
      products: [
        P({ 产品编码: 'CP002', 产品名称: '铝板 6061', 规格型号: '1500×3000×2', 型号: 'PL-6061', 数量: 200, '齐套数量(主)': 200, '累计汇报套数(工序单位)': 0, 可用量: 300, 可用量说明: '可用', 现存量: 450, 现存量说明: '充足', 图号: 'T-002', 单重: 2.6, 总重: 520, 需求令号: 'REQ-04' }),
      ],
      materials: [
        M({ 材料编码: 'CL001', 材料名称: '45#圆钢', 规格型号: 'Φ45', 预出仓库: '原料仓', 领料工序: '下料', 计量单位: 'kg', 定额生产数量: 1, 定额需用数量: 0.9, 需用数量: 180, '损耗率%': 5, 损耗数量: 9, 计划数量: 189, 累计领用数量: 0, 可用量: 1500, 现存量: 2000, 单重: 1, 总重: 189 }),
      ],
      processes: [
        C({ 加工顺序: 1, 工序编码: 'PX001', 工序名称: '下料', 生产车间: '熔铸车间', 工作中心: 'WC-01 熔铸中心', 设备: '锯床-02', 班组: '下料班', 工人: '王强', 工序完工状态: '未开工', 工序单位: '件', 计划数量: 200, 工价: 2.2, 金额: 440, 关键工序: false, 单位标准工时: 0.04, 计划时间: '', 完成时间: '', 需求令号: 'REQ-04', 子表材料: [] }),
        C({ 加工顺序: 2, 工序编码: 'PX003', 工序名称: '铣削', 生产车间: '熔铸车间', 工作中心: 'WC-03 机加中心', 设备: '加工中心-01', 班组: '铣工班', 工人: '孙涛', 工序完工状态: '未开工', 工序单位: '件', 计划数量: 200, 工价: 6.5, 金额: 1300, 关键工序: true, 单位标准工时: 0.15, 计划时间: '', 完成时间: '', 需求令号: 'REQ-04', 子表材料: [] }),
      ],
    },
  },
]

let MOCK_SEQ = 0

// ---------------- SO_ORDER 销售订单演示数据（4 单，与 MANU_ORDER 演示数据联动） ----------------

const S = (o) => ({ '存货名称.品牌': '', 存货名称: '', 存货编码: '', 规格型号: '', 数量: 0, 销售单位: '件', 单价: 0, '税率%': 13, 含税单价: 0, 金额: 0, 含税金额: 0, 折扣金额: 0, 预计交货日期: '', 现存量: 0, 备注: '', ...o })

let SO_ROWS = [
  {
    编号: 'SO-2026-08-0001', 单据状态: '已审核', 审批状态: '已审批', 创建时间: today + ' 08:30', 更新时间: today + ' 08:35', 发起人编号: 'tplusdemo12855',
    审核人: '系统管理员', 审核日期: today, 审核时间: today + ' 08:35', 变更人: '', 变更日期: '', 审核机器人: '',
    单据编号: 'SO-2026-08-0001', 单据日期: today, 客户: '华东铝业', 客户编码: 'KH001', 结算客户: '华东铝业',
    部门: '销售一部', '部门.负责人': '刘经理', 业务员: '张伟', 项目: '铝棒深加工', 预计交货日期: '2026-08-20', 联系人: '王采购',
    detail: {
      items: [
        S({ '存货名称.品牌': '铝棒', 存货名称: '铝棒 Φ80', 存货编码: 'CP001', 规格型号: 'Φ80×3000', 数量: 200, 销售单位: '件', 单价: 15.5, 含税单价: 17.52, 金额: 3100, 含税金额: 3503, 预计交货日期: '2026-08-20', 现存量: 800 }),
        S({ '存货名称.品牌': '铝板', 存货名称: '铝板 6061', 存货编码: 'CP002', 规格型号: '1500×3000×2', 数量: 100, 销售单位: '件', 单价: 12.8, 含税单价: 14.46, 金额: 1280, 含税金额: 1446.4, 预计交货日期: '2026-08-20', 现存量: 450 }),
      ],
    },
  },
  {
    编号: 'SO-2026-08-0002', 单据状态: '已审核', 审批状态: '已审批', 创建时间: '2026-08-12 09:00', 更新时间: '2026-08-12 09:10', 发起人编号: 'tplusdemo12855',
    审核人: '系统管理员', 审核日期: '2026-08-12', 审核时间: '2026-08-12 09:10', 变更人: '', 变更日期: '', 审核机器人: '',
    单据编号: 'SO-2026-08-0002', 单据日期: '2026-08-12', 客户: '中天精工', 客户编码: 'KH002', 结算客户: '中天精工',
    部门: '销售二部', '部门.负责人': '赵经理', 业务员: '李娜', 项目: '', 预计交货日期: '2026-08-15', 联系人: '孙采购',
    detail: {
      items: [
        S({ '存货名称.品牌': '铝板', 存货名称: '铝板 6061', 存货编码: 'CP002', 规格型号: '1500×3000×2', 数量: 500, 销售单位: '件', 单价: 12.8, 含税单价: 14.46, 金额: 6400, 含税金额: 7232, 预计交货日期: '2026-08-15', 现存量: 450 }),
        S({ '存货名称.品牌': '箱体', 存货名称: '减速箱体 A', 存货编码: 'CP004', 规格型号: 'JS-400', 数量: 50, 销售单位: '件', 单价: 260, 含税单价: 293.8, 金额: 13000, 含税金额: 14690, 预计交货日期: '2026-08-15', 现存量: 120 }),
      ],
    },
  },
  {
    编号: 'SO-2026-08-0003', 单据状态: '草稿', 创建时间: today + ' 10:00', 更新时间: today + ' 10:00', 发起人编号: 'tplusdemo12855',
    审核人: '', 审核日期: '', 审核时间: '', 变更人: '', 变更日期: '', 审核机器人: '',
    单据编号: 'SO-2026-08-0003', 单据日期: today, 客户: '西部材料', 客户编码: 'KH003', 结算客户: '西部材料',
    部门: '销售一部', '部门.负责人': '刘经理', 业务员: '王芳', 项目: '', 预计交货日期: '2026-08-28', 联系人: '',
    detail: {
      items: [
        S({ '存货名称.品牌': '型材', 存货名称: '铝型材-散热片', 存货编码: 'CP003', 规格型号: 'XD-6063-T5', 数量: 300, 销售单位: '件', 单价: 22.6, 含税单价: 25.54, 金额: 6780, 含税金额: 7661.4, 预计交货日期: '2026-08-28', 现存量: 600 }),
      ],
    },
  },
  {
    编号: 'SO-2026-08-0004', 单据状态: '已审核', 审批状态: '已审批', 创建时间: '2026-08-11 10:00', 更新时间: '2026-08-11 10:05', 发起人编号: 'tplusdemo12855',
    审核人: '系统管理员', 审核日期: '2026-08-11', 审核时间: '2026-08-11 10:05', 变更人: '', 变更日期: '', 审核机器人: '',
    单据编号: 'SO-2026-08-0004', 单据日期: '2026-08-11', 客户: '西部材料', 客户编码: 'KH003', 结算客户: '西部材料',
    部门: '国际部', '部门.负责人': '周经理', 业务员: '陈强', 项目: '散热片批量', 预计交货日期: '2026-08-22', 联系人: '李采购',
    detail: {
      items: [
        S({ '存货名称.品牌': '型材', 存货名称: '铝型材-散热片', 存货编码: 'CP003', 规格型号: 'XD-6063-T5', 数量: 300, 销售单位: '件', 单价: 22.6, 含税单价: 25.54, 金额: 6780, 含税金额: 7661.4, 预计交货日期: '2026-08-22', 现存量: 600 }),
        S({ '存货名称.品牌': '轴套', 存货名称: '轴套 C', 存货编码: 'CP005', 规格型号: 'ZT-C-30', 数量: 800, 销售单位: '件', 单价: 6.8, 含税单价: 7.68, 金额: 5440, 含税金额: 6144, 预计交货日期: '2026-08-22', 现存量: 1600 }),
      ],
    },
  },
]

// 销售订单自动单号：SO-yyyy-MM-####
function nextSoNo() {
  const prefix = `SO-${today}-`
  const nums = SO_ROWS.map((r) => r['单据编号'] || '').filter((s) => s.startsWith(prefix)).map((s) => Number(s.slice(prefix.length))).filter((n) => !Number.isNaN(n))
  return prefix + String((nums.length ? Math.max(...nums) : 0) + 1).padStart(4, '0')
}

// 销售订单列表展平：明细行（对齐 T+ 列表按明细行展示）
function flattenSoRows() {
  const out = []
  for (const r of SO_ROWS) {
    const { detail, ...head } = r
    for (const it of detail?.items || []) {
      out.push({ ...head, ...it, 子表数量: 1 })
    }
  }
  out.sort((a, b) => (a['编号'] < b['编号'] ? 1 : -1))
  return out
}

function mockDelay(ms = 150) {
  return new Promise((r) => setTimeout(r, ms))
}

// ==================== BASE_CONFIGS 基础档案面板（2026-08-14 按实测 T+ 基础设置复刻） ====================
// 来源：T+ 基础设置实测抓取（BaseInfoList/OpenList/DoubleList），字段/列对齐真实面板，种子联动 MO/SO/库存
const BASE_CONFIGS = {
  DEPT: {
    config: {
    "metadata": {
        "panelCode": "DEPT",
        "panelName": "部门",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "部门列表",
                    "queryFields": [
                        {
                            "dataName": "部门编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "部门名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "部门编码",
                                "部门名称",
                                "负责人",
                                "停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "部门",
                    "fieldNames": "部门编码,部门名称,负责人,停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "部门编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "部门名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "负责人",
                "dataType": "文本"
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "DEPT-001",
        "部门编码": "D01",
        "部门名称": "总经办",
        "负责人": "系统管理员",
        "停用": false
    },
    {
        "编号": "DEPT-002",
        "部门编码": "D02",
        "部门名称": "销售一部",
        "负责人": "刘经理",
        "停用": false
    },
    {
        "编号": "DEPT-003",
        "部门编码": "D03",
        "部门名称": "销售二部",
        "负责人": "赵经理",
        "停用": false
    },
    {
        "编号": "DEPT-004",
        "部门编码": "D04",
        "部门名称": "国际部",
        "负责人": "周经理",
        "停用": false
    },
    {
        "编号": "DEPT-005",
        "部门编码": "D05",
        "部门名称": "熔铸车间",
        "负责人": "王强",
        "停用": false
    },
    {
        "编号": "DEPT-006",
        "部门编码": "D06",
        "部门名称": "轧制车间",
        "负责人": "李丽",
        "停用": false
    },
    {
        "编号": "DEPT-007",
        "部门编码": "D07",
        "部门名称": "精整车间",
        "负责人": "孙涛",
        "停用": false
    },
    {
        "编号": "DEPT-008",
        "部门编码": "D08",
        "部门名称": "测试车间",
        "负责人": "赵刚",
        "停用": false
    },
    {
        "编号": "DEPT-009",
        "部门编码": "D09",
        "部门名称": "质检部",
        "负责人": "赵刚",
        "停用": false
    },
    {
        "编号": "DEPT-010",
        "部门编码": "D10",
        "部门名称": "仓储部",
        "负责人": "陈仓管",
        "停用": false
    }
],
  },
  EMP: {
    config: {
    "metadata": {
        "panelCode": "EMP",
        "panelName": "员工",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "员工列表",
                    "queryFields": [
                        {
                            "dataName": "员工编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "员工名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "员工编码",
                                "员工名称",
                                "所属部门",
                                "业务员",
                                "证件类型",
                                "证件号码",
                                "职务",
                                "职称",
                                "办公电话",
                                "手机",
                                "停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "员工",
                    "fieldNames": "员工编码,员工名称,所属部门,业务员,证件类型,证件号码,职务,职称,办公电话,手机,停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "员工编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "员工名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "所属部门",
                "dataType": "下拉框",
                "options": [
                    "总经办",
                    "销售一部",
                    "销售二部",
                    "国际部",
                    "熔铸车间",
                    "轧制车间",
                    "精整车间",
                    "测试车间",
                    "质检部",
                    "仓储部"
                ]
            },
            {
                "dataName": "业务员",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "证件类型",
                "dataType": "文本"
            },
            {
                "dataName": "证件号码",
                "dataType": "文本"
            },
            {
                "dataName": "职务",
                "dataType": "文本"
            },
            {
                "dataName": "职称",
                "dataType": "文本"
            },
            {
                "dataName": "办公电话",
                "dataType": "文本"
            },
            {
                "dataName": "手机",
                "dataType": "文本"
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "EMP-001",
        "员工编码": "E001",
        "员工名称": "张伟",
        "所属部门": "销售一部",
        "业务员": true,
        "证件类型": "身份证",
        "证件号码": "110101198801010011",
        "职务": "业务员",
        "职称": "初级",
        "办公电话": "010-88001",
        "手机": "13800000001",
        "停用": false
    },
    {
        "编号": "EMP-002",
        "员工编码": "E002",
        "员工名称": "李娜",
        "所属部门": "销售二部",
        "业务员": true,
        "证件类型": "身份证",
        "证件号码": "110101198902020022",
        "职务": "业务员",
        "职称": "中级",
        "办公电话": "010-88002",
        "手机": "13800000002",
        "停用": false
    },
    {
        "编号": "EMP-003",
        "员工编码": "E003",
        "员工名称": "王芳",
        "所属部门": "销售一部",
        "业务员": true,
        "证件类型": "身份证",
        "证件号码": "110101199003030033",
        "职务": "业务员",
        "职称": "初级",
        "办公电话": "010-88003",
        "手机": "13800000003",
        "停用": false
    },
    {
        "编号": "EMP-004",
        "员工编码": "E004",
        "员工名称": "陈强",
        "所属部门": "国际部",
        "业务员": true,
        "证件类型": "身份证",
        "证件号码": "110101199104040044",
        "职务": "业务主管",
        "职称": "高级",
        "办公电话": "010-88004",
        "手机": "13800000004",
        "停用": false
    },
    {
        "编号": "EMP-005",
        "员工编码": "E005",
        "员工名称": "王强",
        "所属部门": "熔铸车间",
        "业务员": false,
        "证件类型": "身份证",
        "证件号码": "110101198506060055",
        "职务": "车间主任",
        "职称": "技师",
        "办公电话": "010-88005",
        "手机": "13800000005",
        "停用": false
    },
    {
        "编号": "EMP-006",
        "员工编码": "E006",
        "员工名称": "李丽",
        "所属部门": "轧制车间",
        "业务员": false,
        "证件类型": "身份证",
        "证件号码": "110101198707070066",
        "职务": "班组长",
        "职称": "技师",
        "办公电话": "010-88006",
        "手机": "13800000006",
        "停用": false
    },
    {
        "编号": "EMP-007",
        "员工编码": "E007",
        "员工名称": "孙涛",
        "所属部门": "精整车间",
        "业务员": false,
        "证件类型": "身份证",
        "证件号码": "110101198808080077",
        "职务": "工人",
        "职称": "中级",
        "办公电话": "010-88007",
        "手机": "13800000007",
        "停用": false
    },
    {
        "编号": "EMP-008",
        "员工编码": "E008",
        "员工名称": "赵刚",
        "所属部门": "测试车间",
        "业务员": false,
        "证件类型": "身份证",
        "证件号码": "110101199010100088",
        "职务": "质检员",
        "职称": "中级",
        "办公电话": "010-88008",
        "手机": "13800000008",
        "停用": false
    }
],
  },
  PARTNER: {
    config: {
    "metadata": {
        "panelCode": "PARTNER",
        "panelName": "往来单位",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "往来单位列表",
                    "queryFields": [
                        {
                            "dataName": "往来单位编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "往来单位名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "往来单位编码",
                                "往来单位名称",
                                "性质",
                                "结算客户",
                                "客户价格等级",
                                "分管部门",
                                "分管人员",
                                "建档日期",
                                "应收余额",
                                "应付余额",
                                "预收余额",
                                "预付余额",
                                "往来余额",
                                "停用",
                                "职位"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "往来单位",
                    "fieldNames": "往来单位编码,往来单位名称,性质,结算客户,客户价格等级,分管部门,分管人员,建档日期,应收余额,应付余额,预收余额,预付余额,往来余额,停用,职位",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "往来单位编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "往来单位名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "性质",
                "dataType": "下拉框",
                "options": [
                    "客户",
                    "供应商",
                    "两者"
                ]
            },
            {
                "dataName": "结算客户",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "客户价格等级",
                "dataType": "下拉框",
                "options": [
                    "普通客户",
                    "一级批发",
                    "二级批发",
                    "三级批发",
                    "四级批发",
                    "五级批发"
                ]
            },
            {
                "dataName": "分管部门",
                "dataType": "下拉框",
                "options": [
                    "销售一部",
                    "销售二部",
                    "国际部"
                ]
            },
            {
                "dataName": "分管人员",
                "dataType": "文本"
            },
            {
                "dataName": "建档日期",
                "dataType": "日期"
            },
            {
                "dataName": "应收余额",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "应付余额",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "预收余额",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "预付余额",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "往来余额",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "职位",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "PARTNER-001",
        "往来单位编码": "KH001",
        "往来单位名称": "华东铝业",
        "性质": "客户",
        "结算客户": true,
        "客户价格等级": "一级批发",
        "分管部门": "销售一部",
        "分管人员": "张伟",
        "建档日期": "2026-01-10",
        "应收余额": 85600,
        "应付余额": 0,
        "预收余额": 20000,
        "预付余额": 0,
        "往来余额": 65600,
        "停用": false,
        "职位": ""
    },
    {
        "编号": "PARTNER-002",
        "往来单位编码": "KH002",
        "往来单位名称": "中天精工",
        "性质": "客户",
        "结算客户": true,
        "客户价格等级": "普通客户",
        "分管部门": "销售二部",
        "分管人员": "李娜",
        "建档日期": "2026-02-15",
        "应收余额": 120000,
        "应付余额": 0,
        "预收余额": 0,
        "预付余额": 0,
        "往来余额": 120000,
        "停用": false,
        "职位": ""
    },
    {
        "编号": "PARTNER-003",
        "往来单位编码": "KH003",
        "往来单位名称": "西部材料",
        "性质": "客户",
        "结算客户": true,
        "客户价格等级": "二级批发",
        "分管部门": "国际部",
        "分管人员": "陈强",
        "建档日期": "2026-03-01",
        "应收余额": 45000,
        "应付余额": 0,
        "预收余额": 5000,
        "预付余额": 0,
        "往来余额": 40000,
        "停用": false,
        "职位": ""
    },
    {
        "编号": "PARTNER-004",
        "往来单位编码": "KH004",
        "往来单位名称": "南方重工",
        "性质": "客户",
        "结算客户": true,
        "客户价格等级": "普通客户",
        "分管部门": "销售一部",
        "分管人员": "王芳",
        "建档日期": "2026-04-20",
        "应收余额": 0,
        "应付余额": 0,
        "预收余额": 0,
        "预付余额": 0,
        "往来余额": 0,
        "停用": false,
        "职位": ""
    },
    {
        "编号": "PARTNER-005",
        "往来单位编码": "KH005",
        "往来单位名称": "北方机械",
        "性质": "两者",
        "结算客户": true,
        "客户价格等级": "普通客户",
        "分管部门": "销售二部",
        "分管人员": "李娜",
        "建档日期": "2026-05-05",
        "应收余额": 8000,
        "应付余额": 12000,
        "预收余额": 0,
        "预付余额": 3000,
        "往来余额": -7000,
        "停用": false,
        "职位": ""
    },
    {
        "编号": "PARTNER-006",
        "往来单位编码": "GYS001",
        "往来单位名称": "华东热处理厂",
        "性质": "供应商",
        "结算客户": false,
        "客户价格等级": "普通客户",
        "分管部门": "采购部",
        "分管人员": "孙采购",
        "建档日期": "2026-01-18",
        "应收余额": 0,
        "应付余额": 28000,
        "预收余额": 0,
        "预付余额": 10000,
        "往来余额": 18000,
        "停用": false,
        "职位": ""
    }
],
  },
  UOM: {
    config: {
    "metadata": {
        "panelCode": "UOM",
        "panelName": "计量单位",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "计量单位列表",
                    "queryFields": [
                        {
                            "dataName": "计量单位编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "计量单位名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "计量单位编码",
                                "计量单位名称",
                                "单位类型",
                                "主单位",
                                "换算率"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "计量单位",
                    "fieldNames": "计量单位编码,计量单位名称,单位类型,主单位,换算率",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "计量单位编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "计量单位名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "单位类型",
                "dataType": "下拉框",
                "options": [
                    "单计量",
                    "多计量"
                ]
            },
            {
                "dataName": "主单位",
                "dataType": "文本"
            },
            {
                "dataName": "换算率",
                "dataType": "小数",
                "defaultValue": 1
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "UOM-001",
        "计量单位编码": "U01",
        "计量单位名称": "件",
        "单位类型": "单计量",
        "主单位": "件",
        "换算率": 1
    },
    {
        "编号": "UOM-002",
        "计量单位编码": "U02",
        "计量单位名称": "kg",
        "单位类型": "单计量",
        "主单位": "kg",
        "换算率": 1
    },
    {
        "编号": "UOM-003",
        "计量单位编码": "U03",
        "计量单位名称": "套",
        "单位类型": "单计量",
        "主单位": "套",
        "换算率": 1
    },
    {
        "编号": "UOM-004",
        "计量单位编码": "U04",
        "计量单位名称": "升",
        "单位类型": "单计量",
        "主单位": "升",
        "换算率": 1
    },
    {
        "编号": "UOM-005",
        "计量单位编码": "U05",
        "计量单位名称": "台",
        "单位类型": "单计量",
        "主单位": "台",
        "换算率": 1
    },
    {
        "编号": "UOM-006",
        "计量单位编码": "U06",
        "计量单位名称": "吨",
        "单位类型": "多计量",
        "主单位": "kg",
        "换算率": 1000
    }
],
  },
  INV: {
    config: {
    "metadata": {
        "panelCode": "INV",
        "panelName": "存货",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "存货列表",
                    "queryFields": [
                        {
                            "dataName": "存货编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "存货名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "存货编码",
                                "存货名称",
                                "规格型号",
                                "计价方式",
                                "所属类别",
                                "品牌",
                                "计量单位",
                                "属性",
                                "参考成本",
                                "最新成本",
                                "建档日期",
                                "停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "存货",
                    "fieldNames": "存货编码,存货名称,规格型号,计价方式,所属类别,品牌,计量单位,属性,参考成本,最新成本,建档日期,停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "存货编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "存货名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "规格型号",
                "dataType": "文本"
            },
            {
                "dataName": "计价方式",
                "dataType": "下拉框",
                "options": [
                    "移动平均",
                    "全月平均",
                    "个别计价",
                    "先进先出"
                ]
            },
            {
                "dataName": "所属类别",
                "dataType": "下拉框",
                "options": [
                    "原材料",
                    "辅助材料",
                    "半成品",
                    "产成品",
                    "包装物"
                ]
            },
            {
                "dataName": "品牌",
                "dataType": "文本"
            },
            {
                "dataName": "计量单位",
                "dataType": "下拉框",
                "options": [
                    "件",
                    "kg",
                    "套",
                    "升",
                    "台"
                ]
            },
            {
                "dataName": "属性",
                "dataType": "下拉框",
                "options": [
                    "自制",
                    "外购",
                    "委外",
                    "自制+外购"
                ]
            },
            {
                "dataName": "参考成本",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "最新成本",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "建档日期",
                "dataType": "日期"
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "INV-001",
        "存货编码": "CP001",
        "存货名称": "铝棒 Φ80",
        "规格型号": "Φ80×3000",
        "计价方式": "移动平均",
        "所属类别": "产成品",
        "品牌": "轻铝",
        "计量单位": "件",
        "属性": "自制",
        "参考成本": 10.2,
        "最新成本": 10.2,
        "建档日期": "2026-01-05",
        "停用": false
    },
    {
        "编号": "INV-002",
        "存货编码": "CP002",
        "存货名称": "铝板 6061",
        "规格型号": "1500×3000×2",
        "计价方式": "移动平均",
        "所属类别": "产成品",
        "品牌": "轻铝",
        "计量单位": "件",
        "属性": "自制",
        "参考成本": 8.6,
        "最新成本": 8.6,
        "建档日期": "2026-01-05",
        "停用": false
    },
    {
        "编号": "INV-003",
        "存货编码": "CP003",
        "存货名称": "铝型材-散热片",
        "规格型号": "XD-6063-T5",
        "计价方式": "移动平均",
        "所属类别": "产成品",
        "品牌": "轻铝",
        "计量单位": "件",
        "属性": "自制",
        "参考成本": 18.4,
        "最新成本": 18.4,
        "建档日期": "2026-01-06",
        "停用": false
    },
    {
        "编号": "INV-004",
        "存货编码": "CP004",
        "存货名称": "减速箱体 A",
        "规格型号": "JS-400",
        "计价方式": "个别计价",
        "所属类别": "产成品",
        "品牌": "精工",
        "计量单位": "件",
        "属性": "自制",
        "参考成本": 210,
        "最新成本": 210,
        "建档日期": "2026-01-08",
        "停用": false
    },
    {
        "编号": "INV-005",
        "存货编码": "CP005",
        "存货名称": "轴套 C",
        "规格型号": "ZT-C-30",
        "计价方式": "移动平均",
        "所属类别": "产成品",
        "品牌": "精工",
        "计量单位": "件",
        "属性": "自制",
        "参考成本": 4.6,
        "最新成本": 4.6,
        "建档日期": "2026-01-10",
        "停用": false
    },
    {
        "编号": "INV-006",
        "存货编码": "CL001",
        "存货名称": "45#圆钢",
        "规格型号": "Φ60",
        "计价方式": "移动平均",
        "所属类别": "原材料",
        "品牌": "",
        "计量单位": "kg",
        "属性": "外购",
        "参考成本": 4.2,
        "最新成本": 4.2,
        "建档日期": "2026-01-05",
        "停用": false
    },
    {
        "编号": "INV-007",
        "存货编码": "CL002",
        "存货名称": "6061铝锭",
        "规格型号": "A00",
        "计价方式": "移动平均",
        "所属类别": "原材料",
        "品牌": "",
        "计量单位": "kg",
        "属性": "外购",
        "参考成本": 12.8,
        "最新成本": 12.8,
        "建档日期": "2026-01-05",
        "停用": false
    },
    {
        "编号": "INV-008",
        "存货编码": "CL003",
        "存货名称": "6063铝棒",
        "规格型号": "Φ120",
        "计价方式": "移动平均",
        "所属类别": "原材料",
        "品牌": "",
        "计量单位": "kg",
        "属性": "外购",
        "参考成本": 13.5,
        "最新成本": 13.5,
        "建档日期": "2026-01-05",
        "停用": false
    },
    {
        "编号": "INV-009",
        "存货编码": "CL004",
        "存货名称": "切削液",
        "规格型号": "20L/桶",
        "计价方式": "移动平均",
        "所属类别": "辅助材料",
        "品牌": "",
        "计量单位": "升",
        "属性": "外购",
        "参考成本": 18,
        "最新成本": 18,
        "建档日期": "2026-01-12",
        "停用": false
    },
    {
        "编号": "INV-010",
        "存货编码": "CL005",
        "存货名称": "包装木箱",
        "规格型号": "1200×800",
        "计价方式": "移动平均",
        "所属类别": "包装物",
        "品牌": "",
        "计量单位": "件",
        "属性": "外购",
        "参考成本": 5,
        "最新成本": 5,
        "建档日期": "2026-01-15",
        "停用": false
    }
],
  },
  EQUIP: {
    config: {
    "metadata": {
        "panelCode": "EQUIP",
        "panelName": "设备",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "设备列表",
                    "queryFields": [
                        {
                            "dataName": "设备编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "设备名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "设备编码",
                                "设备名称",
                                "所属部门",
                                "负责人",
                                "产量/小时",
                                "备注",
                                "停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "设备",
                    "fieldNames": "设备编码,设备名称,所属部门,负责人,产量/小时,备注,停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "设备编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "设备名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "所属部门",
                "dataType": "下拉框",
                "options": [
                    "熔铸车间",
                    "轧制车间",
                    "精整车间",
                    "测试车间"
                ]
            },
            {
                "dataName": "负责人",
                "dataType": "文本"
            },
            {
                "dataName": "产量/小时",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "EQUIP-001",
        "设备编码": "SB001",
        "设备名称": "锯床-01",
        "所属部门": "熔铸车间",
        "负责人": "王强",
        "产量/小时": 120,
        "备注": "",
        "停用": false
    },
    {
        "编号": "EQUIP-002",
        "设备编码": "SB002",
        "设备名称": "锯床-02",
        "所属部门": "轧制车间",
        "负责人": "李丽",
        "产量/小时": 120,
        "备注": "",
        "停用": false
    },
    {
        "编号": "EQUIP-003",
        "设备编码": "SB003",
        "设备名称": "数控车床-03",
        "所属部门": "熔铸车间",
        "负责人": "李丽",
        "产量/小时": 80,
        "备注": "",
        "停用": false
    },
    {
        "编号": "EQUIP-004",
        "设备编码": "SB004",
        "设备名称": "加工中心-02",
        "所属部门": "精整车间",
        "负责人": "孙涛",
        "产量/小时": 60,
        "备注": "",
        "停用": false
    },
    {
        "编号": "EQUIP-005",
        "设备编码": "SB005",
        "设备名称": "检测台-01",
        "所属部门": "测试车间",
        "负责人": "赵刚",
        "产量/小时": 200,
        "备注": "",
        "停用": false
    },
    {
        "编号": "EQUIP-006",
        "设备编码": "SB006",
        "设备名称": "检测台-02",
        "所属部门": "测试车间",
        "负责人": "赵刚",
        "产量/小时": 200,
        "备注": "",
        "停用": false
    }
],
  },
  TEAM: {
    config: {
    "metadata": {
        "panelCode": "TEAM",
        "panelName": "班组",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "班组列表",
                    "queryFields": [
                        {
                            "dataName": "班组编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "班组名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "班组编码",
                                "班组名称",
                                "所属部门",
                                "备注",
                                "是否停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "班组",
                    "fieldNames": "班组编码,班组名称,所属部门,备注,是否停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "班组编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "班组名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "所属部门",
                "dataType": "下拉框",
                "options": [
                    "熔铸车间",
                    "轧制车间",
                    "精整车间",
                    "测试车间"
                ]
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            },
            {
                "dataName": "是否停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "TEAM-001",
        "班组编码": "BZ001",
        "班组名称": "下料班",
        "所属部门": "熔铸车间",
        "备注": "",
        "是否停用": false
    },
    {
        "编号": "TEAM-002",
        "班组编码": "BZ002",
        "班组名称": "车工班",
        "所属部门": "熔铸车间",
        "备注": "",
        "是否停用": false
    },
    {
        "编号": "TEAM-003",
        "班组编码": "BZ003",
        "班组名称": "铣工班",
        "所属部门": "精整车间",
        "备注": "",
        "是否停用": false
    },
    {
        "编号": "TEAM-004",
        "班组编码": "BZ004",
        "班组名称": "热处理班",
        "所属部门": "轧制车间",
        "备注": "委外",
        "是否停用": false
    },
    {
        "编号": "TEAM-005",
        "班组编码": "BZ005",
        "班组名称": "质检班",
        "所属部门": "测试车间",
        "备注": "",
        "是否停用": false
    }
],
  },
  WC: {
    config: {
    "metadata": {
        "panelCode": "WC",
        "panelName": "工作中心",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "工作中心列表",
                    "queryFields": [
                        {
                            "dataName": "工作中心编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "工作中心名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "工作中心编码",
                                "工作中心名称",
                                "简称",
                                "所属分类",
                                "所属部门",
                                "负责人",
                                "班组",
                                "工人",
                                "设备",
                                "产量/小时",
                                "工作时间取值",
                                "工作时间（小时）",
                                "加班时间（小时）",
                                "备注",
                                "停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "工作中心",
                    "fieldNames": "工作中心编码,工作中心名称,简称,所属分类,所属部门,负责人,班组,工人,设备,产量/小时,工作时间取值,工作时间（小时）,加班时间（小时）,备注,停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "工作中心编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "工作中心名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "简称",
                "dataType": "文本"
            },
            {
                "dataName": "所属分类",
                "dataType": "下拉框",
                "options": [
                    "生产线",
                    "加工中心",
                    "检测中心"
                ]
            },
            {
                "dataName": "所属部门",
                "dataType": "下拉框",
                "options": [
                    "熔铸车间",
                    "轧制车间",
                    "精整车间",
                    "测试车间"
                ]
            },
            {
                "dataName": "负责人",
                "dataType": "文本"
            },
            {
                "dataName": "班组",
                "dataType": "下拉框",
                "options": [
                    "下料班",
                    "车工班",
                    "铣工班",
                    "热处理班",
                    "质检班"
                ]
            },
            {
                "dataName": "工人",
                "dataType": "文本"
            },
            {
                "dataName": "设备",
                "dataType": "文本"
            },
            {
                "dataName": "产量/小时",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "工作时间取值",
                "dataType": "下拉框",
                "options": [
                    "日历时间",
                    "班次时间"
                ]
            },
            {
                "dataName": "工作时间（小时）",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "加班时间（小时）",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "WC-001",
        "工作中心编码": "WC-01",
        "工作中心名称": "熔铸中心",
        "简称": "RZ",
        "所属分类": "生产线",
        "所属部门": "熔铸车间",
        "负责人": "王强",
        "班组": "下料班",
        "工人": "王强",
        "设备": "锯床-01",
        "产量/小时": 120,
        "工作时间取值": "班次时间",
        "工作时间（小时）": 8,
        "加班时间（小时）": 2,
        "备注": "",
        "停用": false
    },
    {
        "编号": "WC-002",
        "工作中心编码": "WC-02",
        "工作中心名称": "轧制中心",
        "简称": "ZZ",
        "所属分类": "生产线",
        "所属部门": "轧制车间",
        "负责人": "李丽",
        "班组": "热处理班",
        "工人": "李丽",
        "设备": "锯床-02",
        "产量/小时": 100,
        "工作时间取值": "班次时间",
        "工作时间（小时）": 8,
        "加班时间（小时）": 0,
        "备注": "",
        "停用": false
    },
    {
        "编号": "WC-003",
        "工作中心编码": "WC-03",
        "工作中心名称": "机加中心",
        "简称": "JJ",
        "所属分类": "加工中心",
        "所属部门": "精整车间",
        "负责人": "孙涛",
        "班组": "车工班",
        "工人": "孙涛",
        "设备": "数控车床-03",
        "产量/小时": 60,
        "工作时间取值": "班次时间",
        "工作时间（小时）": 8,
        "加班时间（小时）": 1,
        "备注": "",
        "停用": false
    },
    {
        "编号": "WC-004",
        "工作中心编码": "WC-04",
        "工作中心名称": "检测中心",
        "简称": "JC",
        "所属分类": "检测中心",
        "所属部门": "测试车间",
        "负责人": "赵刚",
        "班组": "质检班",
        "工人": "赵刚",
        "设备": "检测台-01",
        "产量/小时": 200,
        "工作时间取值": "日历时间",
        "工作时间（小时）": 8,
        "加班时间（小时）": 0,
        "备注": "",
        "停用": false
    }
],
  },
  OP: {
    config: {
    "metadata": {
        "panelCode": "OP",
        "panelName": "工序",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "工序列表",
                    "queryFields": [
                        {
                            "dataName": "工序编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "工序名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "工序编码",
                                "工序名称",
                                "默认车间",
                                "关键工序",
                                "加工方式",
                                "标准合格率%",
                                "按辅单位计价",
                                "辅单位",
                                "换算率",
                                "默认工资类型",
                                "计件依据",
                                "备注",
                                "是否停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "工序",
                    "fieldNames": "工序编码,工序名称,默认车间,关键工序,加工方式,标准合格率%,按辅单位计价,辅单位,换算率,默认工资类型,计件依据,备注,是否停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "工序编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "工序名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "默认车间",
                "dataType": "下拉框",
                "options": [
                    "熔铸车间",
                    "轧制车间",
                    "精整车间",
                    "测试车间"
                ]
            },
            {
                "dataName": "关键工序",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "加工方式",
                "dataType": "下拉框",
                "options": [
                    "自制",
                    "委外"
                ]
            },
            {
                "dataName": "标准合格率%",
                "dataType": "小数",
                "defaultValue": 100
            },
            {
                "dataName": "按辅单位计价",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "辅单位",
                "dataType": "下拉框",
                "options": [
                    "件",
                    "kg"
                ]
            },
            {
                "dataName": "换算率",
                "dataType": "小数",
                "defaultValue": 1
            },
            {
                "dataName": "默认工资类型",
                "dataType": "下拉框",
                "options": [
                    "计件",
                    "计时"
                ]
            },
            {
                "dataName": "计件依据",
                "dataType": "下拉框",
                "options": [
                    "合格数量",
                    "完工数量"
                ]
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            },
            {
                "dataName": "是否停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "OP-001",
        "工序编码": "PX001",
        "工序名称": "下料",
        "默认车间": "熔铸车间",
        "关键工序": false,
        "加工方式": "自制",
        "标准合格率%": 100,
        "按辅单位计价": false,
        "辅单位": "件",
        "换算率": 1,
        "默认工资类型": "计件",
        "计件依据": "合格数量",
        "备注": "",
        "是否停用": false
    },
    {
        "编号": "OP-002",
        "工序编码": "PX002",
        "工序名称": "车削",
        "默认车间": "熔铸车间",
        "关键工序": true,
        "加工方式": "自制",
        "标准合格率%": 98,
        "按辅单位计价": false,
        "辅单位": "件",
        "换算率": 1,
        "默认工资类型": "计件",
        "计件依据": "合格数量",
        "备注": "",
        "是否停用": false
    },
    {
        "编号": "OP-003",
        "工序编码": "PX003",
        "工序名称": "铣削",
        "默认车间": "精整车间",
        "关键工序": true,
        "加工方式": "自制",
        "标准合格率%": 97,
        "按辅单位计价": false,
        "辅单位": "件",
        "换算率": 1,
        "默认工资类型": "计件",
        "计件依据": "合格数量",
        "备注": "",
        "是否停用": false
    },
    {
        "编号": "OP-004",
        "工序编码": "PX005",
        "工序名称": "热处理",
        "默认车间": "轧制车间",
        "关键工序": true,
        "加工方式": "委外",
        "标准合格率%": 100,
        "按辅单位计价": true,
        "辅单位": "kg",
        "换算率": 1,
        "默认工资类型": "计时",
        "计件依据": "完工数量",
        "备注": "委外工序",
        "是否停用": false
    },
    {
        "编号": "OP-005",
        "工序编码": "PX007",
        "工序名称": "检验",
        "默认车间": "测试车间",
        "关键工序": false,
        "加工方式": "自制",
        "标准合格率%": 100,
        "按辅单位计价": false,
        "辅单位": "件",
        "换算率": 1,
        "默认工资类型": "计时",
        "计件依据": "完工数量",
        "备注": "",
        "是否停用": false
    }
],
  },
  WH: {
    config: {
    "metadata": {
        "panelCode": "WH",
        "panelName": "仓库",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "仓库列表",
                    "queryFields": [
                        {
                            "dataName": "仓库编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "仓库名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "仓库编码",
                                "仓库名称",
                                "仓库地址",
                                "负责人",
                                "备注",
                                "停用",
                                "允许零库存出库"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "仓库",
                    "fieldNames": "仓库编码,仓库名称,仓库地址,负责人,备注,停用,允许零库存出库",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "仓库编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "仓库名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "仓库地址",
                "dataType": "文本"
            },
            {
                "dataName": "负责人",
                "dataType": "文本"
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "允许零库存出库",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "WH-001",
        "仓库编码": "CK01",
        "仓库名称": "原料仓",
        "仓库地址": "厂区东侧",
        "负责人": "陈仓管",
        "备注": "",
        "停用": false,
        "允许零库存出库": false
    },
    {
        "编号": "WH-002",
        "仓库编码": "CK02",
        "仓库名称": "辅料仓",
        "仓库地址": "厂区东侧",
        "负责人": "陈仓管",
        "备注": "",
        "停用": false,
        "允许零库存出库": false
    },
    {
        "编号": "WH-003",
        "仓库编码": "CK03",
        "仓库名称": "成品仓",
        "仓库地址": "厂区西侧",
        "负责人": "陈仓管",
        "备注": "",
        "停用": false,
        "允许零库存出库": false
    },
    {
        "编号": "WH-004",
        "仓库编码": "CK04",
        "仓库名称": "半成品仓",
        "仓库地址": "车间中部",
        "负责人": "陈仓管",
        "备注": "",
        "停用": false,
        "允许零库存出库": false
    },
    {
        "编号": "WH-005",
        "仓库编码": "CK05",
        "仓库名称": "不良品仓",
        "仓库地址": "厂区南侧",
        "负责人": "陈仓管",
        "备注": "",
        "停用": false,
        "允许零库存出库": false
    }
],
  },
  REGION: {
    config: {
    "metadata": {
        "panelCode": "REGION",
        "panelName": "地区",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "地区列表",
                    "queryFields": [
                        {
                            "dataName": "地区编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "地区名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "地区编码",
                                "地区名称"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "地区",
                    "fieldNames": "地区编码,地区名称",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "地区编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "地区名称",
                "dataType": "文本",
                "isRequired": true
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "REGION-001",
        "地区编码": "R01",
        "地区名称": "广东省"
    },
    {
        "编号": "REGION-002",
        "地区编码": "R02",
        "地区名称": "湖南省"
    },
    {
        "编号": "REGION-003",
        "地区编码": "R03",
        "地区名称": "华东区"
    },
    {
        "编号": "REGION-004",
        "地区编码": "R04",
        "地区名称": "华北区"
    }
],
  },
  PROJ: {
    config: {
    "metadata": {
        "panelCode": "PROJ",
        "panelName": "项目",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "项目列表",
                    "queryFields": [
                        {
                            "dataName": "项目编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "项目名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "项目编码",
                                "项目名称",
                                "停用",
                                "所属类别"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "项目",
                    "fieldNames": "项目编码,项目名称,停用,所属类别",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "项目编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "项目名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "所属类别",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "PROJ-001",
        "项目编码": "XM001",
        "项目名称": "铝棒深加工",
        "停用": false,
        "所属类别": "工程项目"
    },
    {
        "编号": "PROJ-002",
        "项目编码": "XM002",
        "项目名称": "散热片批量",
        "停用": false,
        "所属类别": "研发"
    },
    {
        "编号": "PROJ-003",
        "项目编码": "XM003",
        "项目名称": "厂房扩建",
        "停用": true,
        "所属类别": "工程项目"
    }
],
  },
  REJECT: {
    config: {
    "metadata": {
        "panelCode": "REJECT",
        "panelName": "不合格原因",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "不合格原因列表",
                    "queryFields": [
                        {
                            "dataName": "不合格原因编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "不合格原因",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "不合格原因编码",
                                "不合格原因",
                                "备注",
                                "停用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "不合格原因",
                    "fieldNames": "不合格原因编码,不合格原因,备注,停用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "不合格原因编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "不合格原因",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            },
            {
                "dataName": "停用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "REJECT-001",
        "不合格原因编码": "BH01",
        "不合格原因": "公差偏小",
        "备注": "",
        "停用": false
    },
    {
        "编号": "REJECT-002",
        "不合格原因编码": "BH02",
        "不合格原因": "毛刺多",
        "备注": "",
        "停用": false
    },
    {
        "编号": "REJECT-003",
        "不合格原因编码": "BH03",
        "不合格原因": "尺寸不良",
        "备注": "",
        "停用": false
    },
    {
        "编号": "REJECT-004",
        "不合格原因编码": "BH04",
        "不合格原因": "油污",
        "备注": "",
        "停用": false
    }
],
  },
  PARTNER_INV: {
    config: {
    "metadata": {
        "panelCode": "PARTNER_INV",
        "panelName": "往来单位存货设置",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "往来单位存货设置列表",
                    "queryFields": [
                        {
                            "dataName": "往来单位编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "往来单位名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "往来单位编码",
                                "往来单位名称",
                                "往来单位简称",
                                "往来单位分类",
                                "存货编码",
                                "存货名称",
                                "规格型号",
                                "存货分类",
                                "计量单位",
                                "往来单位存货编码",
                                "往来单位存货名称",
                                "开票名称",
                                "开票规格型号",
                                "开票计量单位"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "往来单位存货设置",
                    "fieldNames": "往来单位编码,往来单位名称,往来单位简称,往来单位分类,存货编码,存货名称,规格型号,存货分类,计量单位,往来单位存货编码,往来单位存货名称,开票名称,开票规格型号,开票计量单位",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "往来单位编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "往来单位名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "往来单位简称",
                "dataType": "文本"
            },
            {
                "dataName": "往来单位分类",
                "dataType": "文本"
            },
            {
                "dataName": "存货编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "存货名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "规格型号",
                "dataType": "文本"
            },
            {
                "dataName": "存货分类",
                "dataType": "文本"
            },
            {
                "dataName": "计量单位",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "往来单位存货编码",
                "dataType": "文本"
            },
            {
                "dataName": "往来单位存货名称",
                "dataType": "文本"
            },
            {
                "dataName": "开票名称",
                "dataType": "文本"
            },
            {
                "dataName": "开票规格型号",
                "dataType": "文本"
            },
            {
                "dataName": "开票计量单位",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "PI-001",
        "往来单位编码": "KH001",
        "往来单位名称": "华东铝业",
        "往来单位简称": "华东",
        "往来单位分类": "客户",
        "存货编码": "CP001",
        "存货名称": "铝棒 Φ80",
        "规格型号": "Φ80×3000",
        "存货分类": "产成品",
        "计量单位": "件",
        "往来单位存货编码": "HD-CP001",
        "往来单位存货名称": "铝棒",
        "开票名称": "铝棒 Φ80",
        "开票规格型号": "Φ80×3000",
        "开票计量单位": "件"
    },
    {
        "编号": "PI-002",
        "往来单位编码": "KH001",
        "往来单位名称": "华东铝业",
        "往来单位简称": "华东",
        "往来单位分类": "客户",
        "存货编码": "CP002",
        "存货名称": "铝板 6061",
        "规格型号": "1500×3000×2",
        "存货分类": "产成品",
        "计量单位": "件",
        "往来单位存货编码": "HD-CP002",
        "往来单位存货名称": "铝板",
        "开票名称": "铝板 6061",
        "开票规格型号": "1500×3000×2",
        "开票计量单位": "件"
    },
    {
        "编号": "PI-003",
        "往来单位编码": "KH002",
        "往来单位名称": "中天精工",
        "往来单位简称": "中天",
        "往来单位分类": "客户",
        "存货编码": "CP004",
        "存货名称": "减速箱体 A",
        "规格型号": "JS-400",
        "存货分类": "产成品",
        "计量单位": "件",
        "往来单位存货编码": "ZT-JS400",
        "往来单位存货名称": "箱体",
        "开票名称": "减速箱体",
        "开票规格型号": "JS-400",
        "开票计量单位": "件"
    }
],
  },
  OP_CONV: {
    config: {
    "metadata": {
        "panelCode": "OP_CONV",
        "panelName": "工序辅单位换算率设置",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "工序辅单位换算率设置列表",
                    "queryFields": [
                        {
                            "dataName": "存货编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "存货名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "存货编码",
                                "存货名称",
                                "规格型号",
                                "工序名称",
                                "工序单位",
                                "按辅单位计价",
                                "工序计价辅单位",
                                "辅单位换算率",
                                "描述",
                                "最近修改日期"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "工序辅单位换算率设置",
                    "fieldNames": "存货编码,存货名称,规格型号,工序名称,工序单位,按辅单位计价,工序计价辅单位,辅单位换算率,描述,最近修改日期",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "存货编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "存货名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "规格型号",
                "dataType": "文本"
            },
            {
                "dataName": "工序名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "工序单位",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "按辅单位计价",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "工序计价辅单位",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "辅单位换算率",
                "dataType": "小数",
                "defaultValue": 1,
                "isRequired": true
            },
            {
                "dataName": "描述",
                "dataType": "文本"
            },
            {
                "dataName": "最近修改日期",
                "dataType": "日期"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "OC-001",
        "存货编码": "CP001",
        "存货名称": "铝棒 Φ80",
        "规格型号": "Φ80×3000",
        "工序名称": "热处理",
        "工序单位": "件",
        "按辅单位计价": true,
        "工序计价辅单位": "kg",
        "辅单位换算率": 8.2,
        "描述": "单重 8.2kg",
        "最近修改日期": "2026-08-10"
    },
    {
        "编号": "OC-002",
        "存货编码": "CP004",
        "存货名称": "减速箱体 A",
        "规格型号": "JS-400",
        "工序名称": "热处理",
        "工序单位": "件",
        "按辅单位计价": true,
        "工序计价辅单位": "kg",
        "辅单位换算率": 42,
        "描述": "单重 42kg",
        "最近修改日期": "2026-08-11"
    }
],
  },
  INV_PRICE: {
    config: {
    "metadata": {
        "panelCode": "INV_PRICE",
        "panelName": "存货价格本",
        "panelCategory": "基础档案",
        "panelState": {
            "dataName": "状态",
            "dataType": "STRING",
            "defaultOptions": [
                "启用",
                "停用"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "存货价格本列表",
                    "queryFields": [
                        {
                            "dataName": "存货编码",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "存货名称",
                            "dataType": "文本"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "存货编码",
                                "存货名称",
                                "规格型号",
                                "品牌",
                                "计量单位",
                                "采购价",
                                "委外价",
                                "零售价",
                                "普通客户价",
                                "一级批发价",
                                "二级批发价",
                                "三级批发价",
                                "四级批发价",
                                "五级批发价",
                                "最新售价",
                                "最低售价",
                                "最新进价",
                                "最高进价",
                                "加价率%",
                                "最近修改日期",
                                "最高委外价",
                                "扣率%",
                                "备注",
                                "操作员",
                                "建档人"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "存货价格本",
                    "fieldNames": "存货编码,存货名称,规格型号,品牌,计量单位,采购价,委外价,零售价,普通客户价,一级批发价,二级批发价,三级批发价,四级批发价,五级批发价,最新售价,最低售价,最新进价,最高进价,加价率%,最近修改日期,最高委外价,扣率%,备注,操作员,建档人",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "放弃"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "导入",
                "actions": [
                    "下载模板",
                    "导入"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "存货编码",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "存货名称",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "规格型号",
                "dataType": "文本"
            },
            {
                "dataName": "品牌",
                "dataType": "文本"
            },
            {
                "dataName": "计量单位",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "采购价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "委外价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "零售价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "普通客户价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "一级批发价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "二级批发价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "三级批发价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "四级批发价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "五级批发价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "最新售价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "最低售价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "最新进价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "最高进价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "加价率%",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "最近修改日期",
                "dataType": "日期"
            },
            {
                "dataName": "最高委外价",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "扣率%",
                "dataType": "小数",
                "defaultValue": 0
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            },
            {
                "dataName": "操作员",
                "dataType": "文本"
            },
            {
                "dataName": "建档人",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "IP-001",
        "存货编码": "CP001",
        "存货名称": "铝棒 Φ80",
        "规格型号": "Φ80×3000",
        "品牌": "轻铝",
        "计量单位": "件",
        "采购价": 10.2,
        "委外价": 0,
        "零售价": 19.5,
        "普通客户价": 15.5,
        "一级批发价": 14.5,
        "二级批发价": 13.8,
        "三级批发价": 13.2,
        "四级批发价": 12.8,
        "五级批发价": 12.2,
        "最新售价": 15.5,
        "最低售价": 12.2,
        "最新进价": 10.2,
        "最高进价": 10.8,
        "加价率%": 15,
        "最近修改日期": "2026-08-10",
        "最高委外价": 0,
        "扣率%": 0,
        "备注": "",
        "操作员": "admin",
        "建档人": "admin"
    },
    {
        "编号": "IP-002",
        "存货编码": "CP002",
        "存货名称": "铝板 6061",
        "规格型号": "1500×3000×2",
        "品牌": "轻铝",
        "计量单位": "件",
        "采购价": 8.6,
        "委外价": 0,
        "零售价": 16,
        "普通客户价": 12.8,
        "一级批发价": 11.8,
        "二级批发价": 11.2,
        "三级批发价": 10.6,
        "四级批发价": 10.2,
        "五级批发价": 9.8,
        "最新售价": 12.8,
        "最低售价": 9.8,
        "最新进价": 8.6,
        "最高进价": 9.2,
        "加价率%": 15,
        "最近修改日期": "2026-08-10",
        "最高委外价": 0,
        "扣率%": 0,
        "备注": "",
        "操作员": "admin",
        "建档人": "admin"
    },
    {
        "编号": "IP-003",
        "存货编码": "CP003",
        "存货名称": "铝型材-散热片",
        "规格型号": "XD-6063-T5",
        "品牌": "轻铝",
        "计量单位": "件",
        "采购价": 18.4,
        "委外价": 0,
        "零售价": 28,
        "普通客户价": 22.6,
        "一级批发价": 21.2,
        "二级批发价": 20.4,
        "三级批发价": 19.6,
        "四级批发价": 18.8,
        "五级批发价": 18.2,
        "最新售价": 22.6,
        "最低售价": 18.2,
        "最新进价": 18.4,
        "最高进价": 19,
        "加价率%": 15,
        "最近修改日期": "2026-08-11",
        "最高委外价": 0,
        "扣率%": 0,
        "备注": "",
        "操作员": "admin",
        "建档人": "admin"
    },
    {
        "编号": "IP-004",
        "存货编码": "CP004",
        "存货名称": "减速箱体 A",
        "规格型号": "JS-400",
        "品牌": "精工",
        "计量单位": "件",
        "采购价": 210,
        "委外价": 0,
        "零售价": 320,
        "普通客户价": 260,
        "一级批发价": 245,
        "二级批发价": 235,
        "三级批发价": 225,
        "四级批发价": 218,
        "五级批发价": 210,
        "最新售价": 260,
        "最低售价": 210,
        "最新进价": 210,
        "最高进价": 225,
        "加价率%": 12,
        "最近修改日期": "2026-08-11",
        "最高委外价": 0,
        "扣率%": 0,
        "备注": "",
        "操作员": "admin",
        "建档人": "admin"
    },
    {
        "编号": "IP-005",
        "存货编码": "CP005",
        "存货名称": "轴套 C",
        "规格型号": "ZT-C-30",
        "品牌": "精工",
        "计量单位": "件",
        "采购价": 4.6,
        "委外价": 0,
        "零售价": 9,
        "普通客户价": 6.8,
        "一级批发价": 6.2,
        "二级批发价": 5.8,
        "三级批发价": 5.4,
        "四级批发价": 5,
        "五级批发价": 4.8,
        "最新售价": 6.8,
        "最低售价": 4.8,
        "最新进价": 4.6,
        "最高进价": 5,
        "加价率%": 15,
        "最近修改日期": "2026-08-12",
        "最高委外价": 0,
        "扣率%": 0,
        "备注": "",
        "操作员": "admin",
        "建档人": "admin"
    }
],
  },
}


// ==================== SYS 系统设置 + 初始化面板（2026-08-14 按实测 T+ 基础设置复刻） ====================
Object.assign(BASE_CONFIGS, {
  SYS_OPT: {
    config: {
    "metadata": {
        "panelCode": "SYS_OPT",
        "panelName": "选项设置",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "选项设置列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": []
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "选项设置",
                    "fieldNames": "数量小数位,单价小数位,金额小数位,允许负库存出库,单据保存后自动审核,启用批号管理,单据编码前缀,默认仓库,默认生产车间",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "保存",
                "actions": [
                    "保存"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "放弃",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "数量小数位",
                "dataType": "文本",
                "defaultValue": "2"
            },
            {
                "dataName": "单价小数位",
                "dataType": "文本",
                "defaultValue": "2"
            },
            {
                "dataName": "金额小数位",
                "dataType": "文本",
                "defaultValue": "2"
            },
            {
                "dataName": "允许负库存出库",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "单据保存后自动审核",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "启用批号管理",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "单据编码前缀",
                "dataType": "文本",
                "defaultValue": "MO/SO/RK/CP/IC/IO/MD/ID"
            },
            {
                "dataName": "默认仓库",
                "dataType": "文本",
                "defaultValue": "成品仓"
            },
            {
                "dataName": "默认生产车间",
                "dataType": "文本",
                "defaultValue": "熔铸车间"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_OPT-001",
        "状态": "启用"
    }
],
  },
  SYS_BOARD_AUTH: {
    config: {
    "metadata": {
        "panelCode": "SYS_BOARD_AUTH",
        "panelName": "看板授权",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "看板授权列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "看板名称",
                                "角色",
                                "授权人",
                                "授权时间",
                                "启用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "看板授权",
                    "fieldNames": "看板名称,角色,授权人,授权时间,启用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "看板名称",
                "dataType": "文本"
            },
            {
                "dataName": "角色",
                "dataType": "文本"
            },
            {
                "dataName": "授权人",
                "dataType": "文本"
            },
            {
                "dataName": "授权时间",
                "dataType": "日期"
            },
            {
                "dataName": "启用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_BA-001",
        "看板名称": "生产看板",
        "角色": "车间主任",
        "授权人": "admin",
        "授权时间": "2026-08-14",
        "启用": true
    },
    {
        "编号": "SYS_BA-002",
        "看板名称": "生产看板",
        "角色": "生产主管",
        "授权人": "admin",
        "授权时间": "2026-08-14",
        "启用": true
    },
    {
        "编号": "SYS_BA-003",
        "看板名称": "生产库存看板",
        "角色": "仓库管理员",
        "授权人": "admin",
        "授权时间": "2026-08-14",
        "启用": true
    }
],
  },
  SYS_BILL_DESIGN: {
    config: {
    "metadata": {
        "panelCode": "SYS_BILL_DESIGN",
        "panelName": "单据设计",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "单据设计列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "单据编码",
                                "单据名称",
                                "版本",
                                "状态",
                                "最近修改时间"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "单据设计",
                    "fieldNames": "单据编码,单据名称,版本,状态,最近修改时间",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "设计",
                "actions": [
                    "打开设计器"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "单据编码",
                "dataType": "文本"
            },
            {
                "dataName": "单据名称",
                "dataType": "文本"
            },
            {
                "dataName": "版本",
                "dataType": "文本"
            },
            {
                "dataName": "状态",
                "dataType": "下拉框",
                "options": [
                    "已发布",
                    "草稿",
                    "修改中"
                ]
            },
            {
                "dataName": "最近修改时间",
                "dataType": "日期"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_BD-001",
        "单据编码": "MP05",
        "单据名称": "生产加工单",
        "版本": "V3.1",
        "状态": "已发布",
        "最近修改时间": "2026-08-14"
    },
    {
        "编号": "SYS_BD-002",
        "单据编码": "SA03",
        "单据名称": "销售订单",
        "版本": "V1.0",
        "状态": "已发布",
        "最近修改时间": "2026-08-14"
    },
    {
        "编号": "SYS_BD-003",
        "单据编码": "ST1001",
        "单据名称": "采购入库单",
        "版本": "V1.0",
        "状态": "已发布",
        "最近修改时间": "2026-08-14"
    }
],
  },
  SYS_CODE: {
    config: {
    "metadata": {
        "panelCode": "SYS_CODE",
        "panelName": "单据编码设置",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "单据编码设置列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "单据名称",
                                "编码前缀",
                                "日期格式",
                                "流水号位数",
                                "当前值",
                                "启用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "单据编码设置",
                    "fieldNames": "单据名称,编码前缀,日期格式,流水号位数,当前值,启用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "单据名称",
                "dataType": "文本"
            },
            {
                "dataName": "编码前缀",
                "dataType": "文本"
            },
            {
                "dataName": "日期格式",
                "dataType": "下拉框",
                "options": [
                    "yyyy-MM-dd",
                    "yyyyMMdd",
                    "yyyy"
                ]
            },
            {
                "dataName": "流水号位数",
                "dataType": "小数",
                "defaultValue": 4
            },
            {
                "dataName": "当前值",
                "dataType": "文本"
            },
            {
                "dataName": "启用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_CODE-001",
        "单据名称": "生产加工单",
        "编码前缀": "MO-",
        "日期格式": "yyyy-MM-dd",
        "流水号位数": 4,
        "当前值": "MO-2026-08-0012",
        "启用": true
    },
    {
        "编号": "SYS_CODE-002",
        "单据名称": "销售订单",
        "编码前缀": "SO-",
        "日期格式": "yyyy-MM-dd",
        "流水号位数": 4,
        "当前值": "SO-2026-08-0005",
        "启用": true
    },
    {
        "编号": "SYS_CODE-003",
        "单据名称": "采购入库单",
        "编码前缀": "RK-",
        "日期格式": "yyyy-MM-dd",
        "流水号位数": 4,
        "当前值": "RK-2026-08-0001",
        "启用": true
    },
    {
        "编号": "SYS_CODE-004",
        "单据名称": "销售出库单",
        "编码前缀": "IO-",
        "日期格式": "yyyy-MM-dd",
        "流水号位数": 4,
        "当前值": "IO-2026-08-0002",
        "启用": true
    }
],
  },
  SYS_PRINT: {
    config: {
    "metadata": {
        "panelCode": "SYS_PRINT",
        "panelName": "打印管理中心",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "打印管理中心列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "模板名称",
                                "所属单据",
                                "模板类型",
                                "状态",
                                "更新时间"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "打印管理中心",
                    "fieldNames": "模板名称,所属单据,模板类型,状态,更新时间",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "模板名称",
                "dataType": "文本"
            },
            {
                "dataName": "所属单据",
                "dataType": "文本"
            },
            {
                "dataName": "模板类型",
                "dataType": "下拉框",
                "options": [
                    "打印模板",
                    "标签模板",
                    "导出模板"
                ]
            },
            {
                "dataName": "状态",
                "dataType": "下拉框",
                "options": [
                    "启用",
                    "停用"
                ]
            },
            {
                "dataName": "更新时间",
                "dataType": "日期"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_PRINT-001",
        "模板名称": "生产加工单默认模板",
        "所属单据": "生产加工单",
        "模板类型": "打印模板",
        "状态": "启用",
        "更新时间": "2026-08-14"
    },
    {
        "编号": "SYS_PRINT-002",
        "模板名称": "销售订单默认模板",
        "所属单据": "销售订单",
        "模板类型": "打印模板",
        "状态": "启用",
        "更新时间": "2026-08-14"
    },
    {
        "编号": "SYS_PRINT-003",
        "模板名称": "工序流转卡模板",
        "所属单据": "生产加工单",
        "模板类型": "标签模板",
        "状态": "启用",
        "更新时间": "2026-08-14"
    }
],
  },
  SYS_ALARM: {
    config: {
    "metadata": {
        "panelCode": "SYS_ALARM",
        "panelName": "预警设置",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "预警设置列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "预警名称",
                                "预警类型",
                                "预警条件",
                                "启用"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "预警设置",
                    "fieldNames": "预警名称,预警类型,预警条件,启用",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "预警名称",
                "dataType": "文本"
            },
            {
                "dataName": "预警类型",
                "dataType": "下拉框",
                "options": [
                    "库存预警",
                    "生产预警",
                    "到期预警"
                ]
            },
            {
                "dataName": "预警条件",
                "dataType": "文本"
            },
            {
                "dataName": "启用",
                "dataType": "是否",
                "defaultValue": false
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_ALARM-001",
        "预警名称": "最低库存预警",
        "预警类型": "库存预警",
        "预警条件": "现存量 <= 安全库存",
        "启用": true
    },
    {
        "编号": "SYS_ALARM-002",
        "预警名称": "生产完工预警",
        "预警类型": "生产预警",
        "预警条件": "预完工日到期未完工",
        "启用": true
    },
    {
        "编号": "SYS_ALARM-003",
        "预警名称": "超额领料预警",
        "预警类型": "生产预警",
        "预警条件": "领用量 > 计划数量",
        "启用": true
    }
],
  },
  SYS_TASK: {
    config: {
    "metadata": {
        "panelCode": "SYS_TASK",
        "panelName": "任务管理",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "任务管理列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "任务名称",
                                "任务类型",
                                "状态",
                                "上次执行时间",
                                "下次执行时间"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "任务管理",
                    "fieldNames": "任务名称,任务类型,状态,上次执行时间,下次执行时间",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "任务名称",
                "dataType": "文本"
            },
            {
                "dataName": "任务类型",
                "dataType": "下拉框",
                "options": [
                    "数据备份",
                    "报表生成",
                    "定时任务"
                ]
            },
            {
                "dataName": "状态",
                "dataType": "下拉框",
                "options": [
                    "运行中",
                    "已停止",
                    "失败"
                ]
            },
            {
                "dataName": "上次执行时间",
                "dataType": "文本"
            },
            {
                "dataName": "下次执行时间",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_TASK-001",
        "任务名称": "每日数据备份",
        "任务类型": "数据备份",
        "状态": "运行中",
        "上次执行时间": "2026-08-14 02:00",
        "下次执行时间": "2026-08-15 02:00"
    },
    {
        "编号": "SYS_TASK-002",
        "任务名称": "生产看板数据刷新",
        "任务类型": "定时任务",
        "状态": "运行中",
        "上次执行时间": "2026-08-14 09:00",
        "下次执行时间": "2026-08-14 10:00"
    }
],
  },
  SYS_SCREEN: {
    config: {
    "metadata": {
        "panelCode": "SYS_SCREEN",
        "panelName": "大屏设备管理",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "大屏设备管理列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "设备编码",
                                "设备名称",
                                "状态",
                                "分辨率",
                                "备注"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "大屏设备管理",
                    "fieldNames": "设备编码,设备名称,状态,分辨率,备注",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "设备编码",
                "dataType": "文本"
            },
            {
                "dataName": "设备名称",
                "dataType": "文本"
            },
            {
                "dataName": "状态",
                "dataType": "下拉框",
                "options": [
                    "在线",
                    "离线"
                ]
            },
            {
                "dataName": "分辨率",
                "dataType": "文本"
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_SCREEN-001",
        "设备编码": "DP001",
        "设备名称": "车间一号大屏",
        "状态": "在线",
        "分辨率": "1920×1080",
        "备注": "生产看板"
    },
    {
        "编号": "SYS_SCREEN-002",
        "设备编码": "DP002",
        "设备名称": "展厅大屏",
        "状态": "离线",
        "分辨率": "3840×2160",
        "备注": ""
    }
],
  },
  SYS_SCREEN_DL: {
    config: {
    "metadata": {
        "panelCode": "SYS_SCREEN_DL",
        "panelName": "大屏客户端下载中心",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "大屏客户端下载中心列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "客户端名称",
                                "版本",
                                "更新日期",
                                "说明"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "大屏客户端下载中心",
                    "fieldNames": "客户端名称,版本,更新日期,说明",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "下载",
                "actions": [
                    "下载客户端"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "客户端名称",
                "dataType": "文本"
            },
            {
                "dataName": "版本",
                "dataType": "文本"
            },
            {
                "dataName": "更新日期",
                "dataType": "日期"
            },
            {
                "dataName": "说明",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_DL-001",
        "客户端名称": "T+ 大屏客户端（Windows）",
        "版本": "v3.2.1",
        "更新日期": "2026-08-01",
        "说明": "支持 Win10/11 x64"
    },
    {
        "编号": "SYS_DL-002",
        "客户端名称": "T+ 大屏客户端（Android）",
        "版本": "v3.1.0",
        "更新日期": "2026-07-20",
        "说明": "支持安卓电视盒子"
    }
],
  },
  SYS_MOBILE: {
    config: {
    "metadata": {
        "panelCode": "SYS_MOBILE",
        "panelName": "移动单据设置",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "移动单据设置列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "单据名称",
                                "单据编码",
                                "移动端启用",
                                "说明"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "移动单据设置",
                    "fieldNames": "单据名称,单据编码,移动端启用,说明",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "单据名称",
                "dataType": "文本"
            },
            {
                "dataName": "单据编码",
                "dataType": "文本"
            },
            {
                "dataName": "移动端启用",
                "dataType": "是否",
                "defaultValue": false
            },
            {
                "dataName": "说明",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_MB-001",
        "单据名称": "工序汇报单",
        "单据编码": "MR10",
        "移动端启用": true,
        "说明": "扫码报工"
    },
    {
        "编号": "SYS_MB-002",
        "单据名称": "生产加工单",
        "单据编码": "MP05",
        "移动端启用": true,
        "说明": ""
    },
    {
        "编号": "SYS_MB-003",
        "单据名称": "销售订单",
        "单据编码": "SA03",
        "移动端启用": false,
        "说明": ""
    }
],
  },
  SYS_MOBILE_TPL: {
    config: {
    "metadata": {
        "panelCode": "SYS_MOBILE_TPL",
        "panelName": "移动模板设置",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "移动模板设置列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "模板名称",
                                "所属单据",
                                "状态",
                                "更新时间"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "移动模板设置",
                    "fieldNames": "模板名称,所属单据,状态,更新时间",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "模板名称",
                "dataType": "文本"
            },
            {
                "dataName": "所属单据",
                "dataType": "文本"
            },
            {
                "dataName": "状态",
                "dataType": "下拉框",
                "options": [
                    "启用",
                    "停用"
                ]
            },
            {
                "dataName": "更新时间",
                "dataType": "日期"
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_MT-001",
        "模板名称": "移动报工模板",
        "所属单据": "工序汇报单",
        "状态": "启用",
        "更新时间": "2026-08-14"
    },
    {
        "编号": "SYS_MT-002",
        "模板名称": "移动审批模板",
        "所属单据": "生产加工单",
        "状态": "启用",
        "更新时间": "2026-08-14"
    }
],
  },
  SYS_PRINT_DEFAULT: {
    config: {
    "metadata": {
        "panelCode": "SYS_PRINT_DEFAULT",
        "panelName": "打印出厂值设置",
        "panelCategory": "系统设置",
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "打印出厂值设置列表",
                    "queryFields": [],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "模板名称",
                                "所属单据",
                                "类型",
                                "状态"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "打印出厂值设置",
                    "fieldNames": "模板名称,所属单据,类型,状态",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "模板名称",
                "dataType": "文本"
            },
            {
                "dataName": "所属单据",
                "dataType": "文本"
            },
            {
                "dataName": "类型",
                "dataType": "下拉框",
                "options": [
                    "出厂模板",
                    "自定义模板"
                ]
            },
            {
                "dataName": "状态",
                "dataType": "下拉框",
                "options": [
                    "启用",
                    "停用"
                ]
            }
        ]
    },
    "detail": {
        "tabs": []
    }
},
    seed: [
    {
        "编号": "SYS_PD-001",
        "模板名称": "出厂-生产加工单",
        "所属单据": "生产加工单",
        "类型": "出厂模板",
        "状态": "启用"
    },
    {
        "编号": "SYS_PD-002",
        "模板名称": "出厂-销售订单",
        "所属单据": "销售订单",
        "类型": "出厂模板",
        "状态": "启用"
    }
],
  },
  INIT_BALANCE: {
    config: {
    "metadata": {
        "panelCode": "INIT_BALANCE",
        "panelName": "库存期初余额",
        "panelCategory": "期初单据",
        "autoCodeField": "期初余额单号",
        "panelState": {
            "dataName": "单据状态",
            "dataType": "STRING",
            "defaultOptions": [
                "草稿",
                "已审核"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "库存期初余额列表",
                    "queryFields": [
                        {
                            "dataName": "期初余额单号",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "单据日期",
                            "dataType": "日期"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "存货编码",
                                "存货名称",
                                "规格型号",
                                "计量单位",
                                "数量",
                                "主单价",
                                "金额",
                                "制单人",
                                "审核人"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "库存期初余额",
                    "fieldNames": "期初余额单号,单据日期,仓库,备注",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "审核"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "审核"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "审核",
                "actions": [
                    "审核",
                    "弃审"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "期初余额单号",
                "dataType": "文本",
                "isRequired": true,
                "autoCode": true
            },
            {
                "dataName": "单据日期",
                "dataType": "日期",
                "defaultValue": "2026-08-14"
            },
            {
                "dataName": "仓库",
                "dataType": "下拉框",
                "options": [
                    "原料仓",
                    "辅料仓",
                    "成品仓",
                    "半成品仓",
                    "不良品仓"
                ],
                "isRequired": true
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": [
            {
                "key": "items",
                "label": "明细",
                "isRequired": true,
                "summaryItems": [],
                "fields": [
                    {
                        "dataName": "存货编码",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "存货名称",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "规格型号",
                        "dataType": "文本"
                    },
                    {
                        "dataName": "计量单位",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "数量",
                        "dataType": "小数",
                        "defaultValue": 0,
                        "isRequired": true
                    },
                    {
                        "dataName": "主单价",
                        "dataType": "小数",
                        "defaultValue": 0
                    },
                    {
                        "dataName": "金额",
                        "dataType": "小数",
                        "defaultValue": 0
                    },
                    {
                        "dataName": "制单人",
                        "dataType": "文本"
                    },
                    {
                        "dataName": "审核人",
                        "dataType": "文本"
                    }
                ]
            }
        ]
    }
},
    seed: [
    {
        "编号": "IB-001",
        "期初余额单号": "IB-2026-08-0001",
        "单据状态": "已审核",
        "单据日期": "2026-08-01",
        "仓库": "原料仓",
        "备注": "期初建账",
        "创建时间": "2026-08-01 09:00",
        "更新时间": "2026-08-01 09:05",
        "发起人编号": "tplusdemo12853",
        "detail": {
            "items": [
                {
                    "存货编码": "CL002",
                    "存货名称": "6061铝锭",
                    "规格型号": "A00",
                    "计量单位": "kg",
                    "数量": 5000,
                    "主单价": 12.8,
                    "金额": 64000,
                    "制单人": "admin",
                    "审核人": "系统管理员"
                },
                {
                    "存货编码": "CL001",
                    "存货名称": "45#圆钢",
                    "规格型号": "Φ60",
                    "计量单位": "kg",
                    "数量": 800,
                    "主单价": 4.2,
                    "金额": 3360,
                    "制单人": "admin",
                    "审核人": "系统管理员"
                },
                {
                    "存货编码": "CL004",
                    "存货名称": "切削液",
                    "规格型号": "20L/桶",
                    "计量单位": "升",
                    "数量": 200,
                    "主单价": 18,
                    "金额": 3600,
                    "制单人": "admin",
                    "审核人": "系统管理员"
                }
            ]
        }
    },
    {
        "编号": "IB-002",
        "期初余额单号": "IB-2026-08-0002",
        "单据状态": "已审核",
        "单据日期": "2026-08-01",
        "仓库": "成品仓",
        "备注": "期初库存",
        "创建时间": "2026-08-01 09:10",
        "更新时间": "2026-08-01 09:15",
        "发起人编号": "tplusdemo12853",
        "detail": {
            "items": [
                {
                    "存货编码": "CP001",
                    "存货名称": "铝棒 Φ80",
                    "规格型号": "Φ80×3000",
                    "计量单位": "件",
                    "数量": 800,
                    "主单价": 10.2,
                    "金额": 8160,
                    "制单人": "admin",
                    "审核人": "系统管理员"
                },
                {
                    "存货编码": "CP002",
                    "存货名称": "铝板 6061",
                    "规格型号": "1500×3000×2",
                    "计量单位": "件",
                    "数量": 450,
                    "主单价": 8.6,
                    "金额": 3870,
                    "制单人": "admin",
                    "审核人": "系统管理员"
                }
            ]
        }
    }
],
  },
  INIT_AP: {
    config: {
    "metadata": {
        "panelCode": "INIT_AP",
        "panelName": "期初暂估入库单",
        "panelCategory": "期初单据",
        "autoCodeField": "期初暂估单号",
        "panelState": {
            "dataName": "单据状态",
            "dataType": "STRING",
            "defaultOptions": [
                "草稿",
                "已审核"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "期初暂估入库单列表",
                    "queryFields": [
                        {
                            "dataName": "期初暂估单号",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "单据日期",
                            "dataType": "日期"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "仓库",
                                "存货名称",
                                "规格型号",
                                "计量单位",
                                "实收数量",
                                "单价",
                                "金额"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "期初暂估入库单",
                    "fieldNames": "期初暂估单号,单据日期,供应商,仓库,备注",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "审核"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "审核"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "审核",
                "actions": [
                    "审核",
                    "弃审"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "期初暂估单号",
                "dataType": "文本",
                "isRequired": true,
                "autoCode": true
            },
            {
                "dataName": "单据日期",
                "dataType": "日期",
                "defaultValue": "2026-08-14"
            },
            {
                "dataName": "供应商",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "仓库",
                "dataType": "下拉框",
                "options": [
                    "原料仓",
                    "辅料仓",
                    "成品仓",
                    "半成品仓",
                    "不良品仓"
                ],
                "isRequired": true
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": [
            {
                "key": "items",
                "label": "明细",
                "isRequired": true,
                "summaryItems": [],
                "fields": [
                    {
                        "dataName": "仓库",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "存货名称",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "规格型号",
                        "dataType": "文本"
                    },
                    {
                        "dataName": "计量单位",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "实收数量",
                        "dataType": "小数",
                        "defaultValue": 0,
                        "isRequired": true
                    },
                    {
                        "dataName": "单价",
                        "dataType": "小数",
                        "defaultValue": 0
                    },
                    {
                        "dataName": "金额",
                        "dataType": "小数",
                        "defaultValue": 0
                    }
                ]
            }
        ]
    }
},
    seed: [
    {
        "编号": "IAP-001",
        "期初暂估单号": "IAP-2026-08-0001",
        "单据状态": "已审核",
        "单据日期": "2026-08-01",
        "供应商": "华东热处理厂",
        "仓库": "原料仓",
        "备注": "",
        "创建时间": "2026-08-01 10:00",
        "更新时间": "2026-08-01 10:05",
        "发起人编号": "tplusdemo12853",
        "detail": {
            "items": [
                {
                    "仓库": "原料仓",
                    "存货名称": "6061铝锭",
                    "规格型号": "A00",
                    "计量单位": "kg",
                    "实收数量": 3000,
                    "单价": 12.8,
                    "金额": 38400
                }
            ]
        }
    }
],
  },
  INIT_AR: {
    config: {
    "metadata": {
        "panelCode": "INIT_AR",
        "panelName": "期初销售出库单",
        "panelCategory": "期初单据",
        "autoCodeField": "期初出库单号",
        "panelState": {
            "dataName": "单据状态",
            "dataType": "STRING",
            "defaultOptions": [
                "草稿",
                "已审核"
            ]
        },
        "panelPageDto": {
            "tablePages": [
                {
                    "tableName": "期初销售出库单列表",
                    "queryFields": [
                        {
                            "dataName": "期初出库单号",
                            "dataType": "文本"
                        },
                        {
                            "dataName": "单据日期",
                            "dataType": "日期"
                        }
                    ],
                    "gridTabs": [
                        {
                            "label": "列表",
                            "rowSource": "rows",
                            "columns": [
                                "仓库",
                                "存货名称",
                                "规格型号",
                                "计量单位",
                                "数量"
                            ]
                        }
                    ],
                    "topBarBtn": [
                        {
                            "buttonName": "新增流程"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "刷新"
                        }
                    ],
                    "rowOperationBarBtn": [],
                    "events": []
                }
            ],
            "formPages": [
                {
                    "formName": "期初销售出库单",
                    "fieldNames": "期初出库单号,单据日期,客户,仓库,备注",
                    "bottomOperationBarBtn": [
                        {
                            "buttonName": "保存"
                        },
                        {
                            "buttonName": "审核"
                        },
                        {
                            "buttonName": "删除"
                        },
                        {
                            "buttonName": "放弃"
                        }
                    ],
                    "events": []
                }
            ]
        },
        "panelButtons": [
            {
                "buttonName": "新增流程"
            },
            {
                "buttonName": "保存"
            },
            {
                "buttonName": "审核"
            },
            {
                "buttonName": "删除"
            },
            {
                "buttonName": "刷新"
            }
        ],
        "buttonGroups": [
            {
                "name": "新增",
                "actions": [
                    "新增"
                ]
            },
            {
                "name": "修改",
                "actions": [
                    "修改"
                ]
            },
            {
                "name": "删除",
                "actions": [
                    "删除"
                ]
            },
            {
                "name": "审核",
                "actions": [
                    "审核",
                    "弃审"
                ]
            },
            {
                "name": "查找",
                "actions": [
                    "查找",
                    "刷新"
                ]
            },
            {
                "name": "打印",
                "actions": [
                    "打印",
                    "预览"
                ]
            },
            {
                "name": "更多",
                "actions": [
                    "复制",
                    "导出",
                    "退出"
                ]
            }
        ],
        "version": "1.0"
    },
    "dataSchema": {
        "type": "object",
        "fields": [
            {
                "dataName": "期初出库单号",
                "dataType": "文本",
                "isRequired": true,
                "autoCode": true
            },
            {
                "dataName": "单据日期",
                "dataType": "日期",
                "defaultValue": "2026-08-14"
            },
            {
                "dataName": "客户",
                "dataType": "文本",
                "isRequired": true
            },
            {
                "dataName": "仓库",
                "dataType": "下拉框",
                "options": [
                    "原料仓",
                    "辅料仓",
                    "成品仓",
                    "半成品仓",
                    "不良品仓"
                ],
                "isRequired": true
            },
            {
                "dataName": "备注",
                "dataType": "文本"
            }
        ]
    },
    "detail": {
        "tabs": [
            {
                "key": "items",
                "label": "明细",
                "isRequired": true,
                "summaryItems": [],
                "fields": [
                    {
                        "dataName": "仓库",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "存货名称",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "规格型号",
                        "dataType": "文本"
                    },
                    {
                        "dataName": "计量单位",
                        "dataType": "文本",
                        "isRequired": true
                    },
                    {
                        "dataName": "数量",
                        "dataType": "小数",
                        "defaultValue": 0,
                        "isRequired": true
                    }
                ]
            }
        ]
    }
},
    seed: [
    {
        "编号": "IAR-001",
        "期初出库单号": "IAR-2026-08-0001",
        "单据状态": "已审核",
        "单据日期": "2026-08-01",
        "客户": "华东铝业",
        "仓库": "成品仓",
        "备注": "",
        "创建时间": "2026-08-01 10:30",
        "更新时间": "2026-08-01 10:35",
        "发起人编号": "tplusdemo12853",
        "detail": {
            "items": [
                {
                    "仓库": "成品仓",
                    "存货名称": "铝棒 Φ80",
                    "规格型号": "Φ80×3000",
                    "计量单位": "件",
                    "数量": 200
                }
            ]
        }
    }
],
  },
})

// ROUTE 工艺路线（DoubleList：表头 + 工序明细，对齐 AA1055）
const ROUTE_CONFIG = {
  "metadata": {
    "panelCode": "ROUTE",
    "panelName": "工艺路线",
    "panelCategory": "单据",
    "autoCodeField": "工艺路线编码",
    "panelState": {
      "dataName": "单据状态",
      "dataType": "STRING",
      "defaultOptions": [
        "草稿",
        "已审核",
        "已中止"
      ]
    },
    "panelPageDto": {
      "tablePages": [
        {
          "tableName": "工艺路线列表",
          "queryFields": [
            {
              "dataName": "工艺路线编码",
              "dataType": "文本"
            },
            {
              "dataName": "工艺路线名称",
              "dataType": "文本"
            }
          ],
          "gridTabs": [
            {
              "label": "列表",
              "rowSource": "rows",
              "columns": [
                "工艺路线编码",
                "工艺路线名称",
                "单据状态",
                "停用",
                "制单人",
                "审核人",
                "审核日期"
              ]
            }
          ],
          "topBarBtn": [
            {
              "buttonName": "新增流程"
            },
            {
              "buttonName": "删除"
            },
            {
              "buttonName": "刷新"
            }
          ],
          "rowOperationBarBtn": [],
          "events": []
        }
      ],
      "formPages": [
        {
          "formName": "工艺路线",
          "fieldNames": "工艺路线编码,工艺路线名称,停用",
          "bottomOperationBarBtn": [
            {
              "buttonName": "保存"
            },
            {
              "buttonName": "审核"
            },
            {
              "buttonName": "弃审"
            },
            {
              "buttonName": "删除"
            },
            {
              "buttonName": "放弃"
            }
          ],
          "events": []
        }
      ]
    },
    "panelButtons": [
      {
        "buttonName": "新增流程"
      },
      {
        "buttonName": "保存"
      },
      {
        "buttonName": "审核"
      },
      {
        "buttonName": "弃审"
      },
      {
        "buttonName": "删除"
      },
      {
        "buttonName": "刷新"
      }
    ],
    "buttonGroups": [
      {
        "name": "新增",
        "actions": [
          "新增"
        ]
      },
      {
        "name": "修改",
        "actions": [
          "修改"
        ]
      },
      {
        "name": "删除",
        "actions": [
          "删除"
        ]
      },
      {
        "name": "审核",
        "actions": [
          "审核",
          "弃审"
        ]
      },
      {
        "name": "查找",
        "actions": [
          "查找",
          "刷新"
        ]
      },
      {
        "name": "打印",
        "actions": [
          "打印",
          "预览"
        ]
      },
      {
        "name": "导入",
        "actions": [
          "下载工艺路线模板",
          "导入工艺路线"
        ]
      },
      {
        "name": "更多",
        "actions": [
          "复制",
          "导出",
          "退出"
        ]
      }
    ],
    "version": "1.0"
  },
  "dataSchema": {
    "type": "object",
    "fields": [
      {
        "dataName": "工艺路线编码",
        "dataType": "文本",
        "isRequired": true,
        "autoCode": true
      },
      {
        "dataName": "工艺路线名称",
        "dataType": "文本",
        "isRequired": true
      },
      {
        "dataName": "停用",
        "dataType": "是否",
        "defaultValue": false
      }
    ]
  },
  "detail": {
    "tabs": [
      {
        "key": "processes",
        "label": "工序明细",
        "isRequired": true,
        "summaryItems": [
          {
            "label": "工序数",
            "field": "加工顺序"
          }
        ],
        "fields": [
          {
            "dataName": "加工顺序",
            "dataType": "整数",
            "defaultValue": 1
          },
          {
            "dataName": "工序编码",
            "dataType": "下拉框",
            "options": [
              "PX001",
              "PX002",
              "PX003",
              "PX005",
              "PX007"
            ]
          },
          {
            "dataName": "工序名称",
            "dataType": "下拉框",
            "options": [
              "下料",
              "车削",
              "铣削",
              "热处理",
              "检验"
            ]
          },
          {
            "dataName": "工作中心",
            "dataType": "下拉框",
            "options": [
              "WC-01 熔铸中心",
              "WC-02 轧制中心",
              "WC-03 机加中心",
              "WC-04 检测中心"
            ]
          },
          {
            "dataName": "设备",
            "dataType": "文本"
          },
          {
            "dataName": "班组",
            "dataType": "下拉框",
            "options": [
              "下料班",
              "车工班",
              "铣工班",
              "热处理班",
              "质检班"
            ]
          },
          {
            "dataName": "单位标准工时",
            "dataType": "小数",
            "defaultValue": 0
          },
          {
            "dataName": "工价",
            "dataType": "小数",
            "defaultValue": 0
          },
          {
            "dataName": "备注",
            "dataType": "文本"
          }
        ]
      }
    ]
  }
}
let ROUTE_ROWS = [
  {
    "编号": "ROUTE-001",
    "工艺路线编码": "GY-001",
    "工艺路线名称": "铝棒 Φ80 加工路线",
    "单据状态": "已审核",
    "停用": false,
    "制单人": "admin",
    "审核人": "系统管理员",
    "审核日期": "2026-08-10",
    "创建时间": "2026-08-10 09:00",
    "更新时间": "2026-08-10 09:05",
    "发起人编号": "tplusdemo12853",
    "detail": {
      "processes": [
        {
          "加工顺序": 1,
          "工序编码": "PX001",
          "工序名称": "下料",
          "工作中心": "WC-01 熔铸中心",
          "设备": "锯床-01",
          "班组": "下料班",
          "单位标准工时": 0.05,
          "工价": 2.5,
          "备注": ""
        },
        {
          "加工顺序": 2,
          "工序编码": "PX002",
          "工序名称": "车削",
          "工作中心": "WC-03 机加中心",
          "设备": "数控车床-03",
          "班组": "车工班",
          "单位标准工时": 0.12,
          "工价": 5.8,
          "备注": ""
        },
        {
          "加工顺序": 3,
          "工序编码": "PX007",
          "工序名称": "检验",
          "工作中心": "WC-04 检测中心",
          "设备": "检测台-01",
          "班组": "质检班",
          "单位标准工时": 0.03,
          "工价": 1.2,
          "备注": ""
        }
      ]
    }
  },
  {
    "编号": "ROUTE-002",
    "工艺路线编码": "GY-002",
    "工艺路线名称": "减速箱体 A 加工路线",
    "单据状态": "已审核",
    "停用": false,
    "制单人": "admin",
    "审核人": "系统管理员",
    "审核日期": "2026-08-11",
    "创建时间": "2026-08-11 10:00",
    "更新时间": "2026-08-11 10:05",
    "发起人编号": "tplusdemo12853",
    "detail": {
      "processes": [
        {
          "加工顺序": 1,
          "工序编码": "PX001",
          "工序名称": "下料",
          "工作中心": "WC-01 熔铸中心",
          "设备": "锯床-01",
          "班组": "下料班",
          "单位标准工时": 0.08,
          "工价": 3,
          "备注": ""
        },
        {
          "加工顺序": 2,
          "工序编码": "PX003",
          "工序名称": "铣削",
          "工作中心": "WC-03 机加中心",
          "设备": "加工中心-02",
          "班组": "铣工班",
          "单位标准工时": 0.2,
          "工价": 8.5,
          "备注": ""
        },
        {
          "加工顺序": 3,
          "工序编码": "PX005",
          "工序名称": "热处理",
          "工作中心": "WC-02 轧制中心",
          "设备": "",
          "班组": "热处理班",
          "单位标准工时": 0.5,
          "工价": 0,
          "备注": "委外"
        },
        {
          "加工顺序": 4,
          "工序编码": "PX007",
          "工序名称": "检验",
          "工作中心": "WC-04 检测中心",
          "设备": "检测台-02",
          "班组": "质检班",
          "单位标准工时": 0.05,
          "工价": 1.5,
          "备注": ""
        }
      ]
    }
  }
]

// BOM 物料清单（表头父件 + 子件明细，对齐 AA1041）
const BOM_CONFIG = {
  "metadata": {
    "panelCode": "BOM",
    "panelName": "物料清单",
    "panelCategory": "单据",
    "autoCodeField": "物料清单编码",
    "panelState": {
      "dataName": "单据状态",
      "dataType": "STRING",
      "defaultOptions": [
        "草稿",
        "已审核",
        "已中止"
      ]
    },
    "panelPageDto": {
      "tablePages": [
        {
          "tableName": "物料清单列表",
          "queryFields": [
            {
              "dataName": "父件编码",
              "dataType": "文本"
            },
            {
              "dataName": "父件名称",
              "dataType": "文本"
            },
            {
              "dataName": "版本号",
              "dataType": "文本"
            }
          ],
          "gridTabs": [
            {
              "label": "列表",
              "rowSource": "rows",
              "columns": [
                "父件编码",
                "父件名称",
                "虚拟件",
                "版本号",
                "计量单位",
                "生产数量",
                "生产车间",
                "预入仓库",
                "默认BOM",
                "制单人",
                "审核日期",
                "创建时间"
              ]
            }
          ],
          "topBarBtn": [
            {
              "buttonName": "新增流程"
            },
            {
              "buttonName": "删除"
            },
            {
              "buttonName": "刷新"
            }
          ],
          "rowOperationBarBtn": [],
          "events": []
        }
      ],
      "formPages": [
        {
          "formName": "物料清单",
          "fieldNames": "父件编码,父件名称,虚拟件,版本号,计量单位,生产数量,生产车间,预入仓库,默认BOM",
          "bottomOperationBarBtn": [
            {
              "buttonName": "保存"
            },
            {
              "buttonName": "审核"
            },
            {
              "buttonName": "弃审"
            },
            {
              "buttonName": "删除"
            },
            {
              "buttonName": "放弃"
            }
          ],
          "events": []
        }
      ]
    },
    "panelButtons": [
      {
        "buttonName": "新增流程"
      },
      {
        "buttonName": "保存"
      },
      {
        "buttonName": "审核"
      },
      {
        "buttonName": "弃审"
      },
      {
        "buttonName": "删除"
      },
      {
        "buttonName": "刷新"
      }
    ],
    "buttonGroups": [
      {
        "name": "新增",
        "actions": [
          "新增"
        ]
      },
      {
        "name": "修改",
        "actions": [
          "修改"
        ]
      },
      {
        "name": "删除",
        "actions": [
          "删除"
        ]
      },
      {
        "name": "审核",
        "actions": [
          "审核",
          "弃审"
        ]
      },
      {
        "name": "查找",
        "actions": [
          "查找",
          "刷新"
        ]
      },
      {
        "name": "打印",
        "actions": [
          "打印",
          "预览"
        ]
      },
      {
        "name": "更多",
        "actions": [
          "复制",
          "BOM展开",
          "导出",
          "退出"
        ]
      }
    ],
    "version": "1.0"
  },
  "dataSchema": {
    "type": "object",
    "fields": [
      {
        "dataName": "物料清单编码",
        "dataType": "文本",
        "isRequired": true,
        "autoCode": true
      },
      {
        "dataName": "父件编码",
        "dataType": "下拉框",
        "isRequired": true,
        "options": [
          "CP001",
          "CP002",
          "CP003",
          "CP004",
          "CP005"
        ]
      },
      {
        "dataName": "父件名称",
        "dataType": "文本",
        "isRequired": true
      },
      {
        "dataName": "虚拟件",
        "dataType": "是否",
        "defaultValue": false
      },
      {
        "dataName": "版本号",
        "dataType": "文本",
        "defaultValue": "V1.0"
      },
      {
        "dataName": "计量单位",
        "dataType": "下拉框",
        "options": [
          "件",
          "kg",
          "套"
        ],
        "defaultValue": "件"
      },
      {
        "dataName": "生产数量",
        "dataType": "小数",
        "defaultValue": 1
      },
      {
        "dataName": "生产车间",
        "dataType": "下拉框",
        "options": [
          "熔铸车间",
          "轧制车间",
          "精整车间",
          "测试车间"
        ]
      },
      {
        "dataName": "预入仓库",
        "dataType": "下拉框",
        "options": [
          "原料仓",
          "辅料仓",
          "成品仓",
          "半成品仓"
        ],
        "defaultValue": "成品仓"
      },
      {
        "dataName": "默认BOM",
        "dataType": "是否",
        "defaultValue": true
      }
    ]
  },
  "detail": {
    "tabs": [
      {
        "key": "children",
        "label": "子件明细",
        "isRequired": true,
        "summaryItems": [
          {
            "label": "子件数",
            "field": "定额数量"
          }
        ],
        "calc": [
          {
            "target": "需用数量",
            "formula": "定额数量 * 生产数量",
            "round": 2
          }
        ],
        "fields": [
          {
            "dataName": "子件编码",
            "dataType": "下拉框",
            "options": [
              "CL001",
              "CL002",
              "CL003",
              "CL004",
              "CL005"
            ]
          },
          {
            "dataName": "子件名称",
            "dataType": "文本"
          },
          {
            "dataName": "规格型号",
            "dataType": "文本"
          },
          {
            "dataName": "计量单位",
            "dataType": "下拉框",
            "options": [
              "kg",
              "件",
              "套",
              "升"
            ]
          },
          {
            "dataName": "定额数量",
            "dataType": "小数",
            "defaultValue": 0
          },
          {
            "dataName": "损耗率%",
            "dataType": "小数",
            "defaultValue": 0
          },
          {
            "dataName": "需用数量",
            "dataType": "小数",
            "computed": true
          },
          {
            "dataName": "备注",
            "dataType": "文本"
          }
        ]
      }
    ]
  }
}
let BOM_ROWS = [
  {
    "编号": "BOM-001",
    "物料清单编码": "BOM-001",
    "父件编码": "CP001",
    "父件名称": "铝棒 Φ80",
    "虚拟件": false,
    "版本号": "V1.0",
    "计量单位": "件",
    "生产数量": 1,
    "生产车间": "熔铸车间",
    "预入仓库": "成品仓",
    "默认BOM": true,
    "单据状态": "已审核",
    "制单人": "admin",
    "审核人": "系统管理员",
    "审核日期": "2026-08-10",
    "创建时间": "2026-08-10 09:00",
    "更新时间": "2026-08-10 09:05",
    "发起人编号": "tplusdemo12853",
    "detail": {
      "children": [
        {
          "子件编码": "CL002",
          "子件名称": "6061铝锭",
          "规格型号": "A00",
          "计量单位": "kg",
          "定额数量": 1.05,
          "损耗率%": 5,
          "需用数量": 1.05,
          "备注": ""
        }
      ]
    }
  },
  {
    "编号": "BOM-002",
    "物料清单编码": "BOM-002",
    "父件编码": "CP003",
    "父件名称": "铝型材-散热片",
    "虚拟件": false,
    "版本号": "V1.0",
    "计量单位": "件",
    "生产数量": 1,
    "生产车间": "精整车间",
    "预入仓库": "成品仓",
    "默认BOM": true,
    "单据状态": "已审核",
    "制单人": "admin",
    "审核人": "系统管理员",
    "审核日期": "2026-08-11",
    "创建时间": "2026-08-11 09:00",
    "更新时间": "2026-08-11 09:05",
    "发起人编号": "tplusdemo12853",
    "detail": {
      "children": [
        {
          "子件编码": "CL003",
          "子件名称": "6063铝棒",
          "规格型号": "Φ120",
          "计量单位": "kg",
          "定额数量": 1.1,
          "损耗率%": 10,
          "需用数量": 1.1,
          "备注": ""
        },
        {
          "子件编码": "CL005",
          "子件名称": "包装木箱",
          "规格型号": "1200×800",
          "计量单位": "件",
          "定额数量": 0.02,
          "损耗率%": 0,
          "需用数量": 0.02,
          "备注": "包装"
        }
      ]
    }
  }
]

// BOM 正/反向查询（CommonList，对齐 AA1046/AA1047）
const BOM_FWD_CONFIG = {
  "metadata": {
    "panelCode": "BOM_FWD",
    "panelName": "物料清单正向查询",
    "panelCategory": "查询",
    "panelPageDto": {
      "tablePages": [
        {
          "tableName": "物料清单正向查询列表",
          "queryFields": [
            {
              "dataName": "父件编码",
              "dataType": "文本"
            },
            {
              "dataName": "子件编码",
              "dataType": "文本"
            }
          ],
          "gridTabs": [
            {
              "label": "列表",
              "rowSource": "rows",
              "columns": [
                "父件编码",
                "父件名称",
                "版本号",
                "子件编码",
                "子件名称",
                "规格型号",
                "计量单位",
                "定额数量",
                "损耗率%",
                "需用数量"
              ]
            }
          ],
          "topBarBtn": [
            {
              "buttonName": "刷新"
            }
          ],
          "rowOperationBarBtn": [],
          "events": []
        }
      ],
      "formPages": []
    },
    "panelButtons": [
      {
        "buttonName": "刷新"
      }
    ],
    "buttonGroups": [
      {
        "name": "查找",
        "actions": [
          "查找",
          "刷新"
        ]
      },
      {
        "name": "更多",
        "actions": [
          "导出",
          "退出"
        ]
      }
    ],
    "version": "1.0"
  },
  "dataSchema": {
    "type": "object",
    "fields": []
  },
  "detail": {
    "tabs": []
  }
}
let BOM_FWD_ROWS = [
  {
    "编号": "BOMF-001",
    "父件编码": "CP001",
    "父件名称": "铝棒 Φ80",
    "版本号": "V1.0",
    "子件编码": "CL002",
    "子件名称": "6061铝锭",
    "规格型号": "A00",
    "计量单位": "kg",
    "定额数量": 1.05,
    "损耗率%": 5,
    "需用数量": 1.05
  },
  {
    "编号": "BOMF-002",
    "父件编码": "CP003",
    "父件名称": "铝型材-散热片",
    "版本号": "V1.0",
    "子件编码": "CL003",
    "子件名称": "6063铝棒",
    "规格型号": "Φ120",
    "计量单位": "kg",
    "定额数量": 1.1,
    "损耗率%": 10,
    "需用数量": 1.1
  },
  {
    "编号": "BOMF-003",
    "父件编码": "CP003",
    "父件名称": "铝型材-散热片",
    "版本号": "V1.0",
    "子件编码": "CL005",
    "子件名称": "包装木箱",
    "规格型号": "1200×800",
    "计量单位": "件",
    "定额数量": 0.02,
    "损耗率%": 0,
    "需用数量": 0.02
  }
]
const BOM_REV_CONFIG = {
  "metadata": {
    "panelCode": "BOM_REV",
    "panelName": "物料清单反向查询",
    "panelCategory": "查询",
    "panelPageDto": {
      "tablePages": [
        {
          "tableName": "物料清单反向查询列表",
          "queryFields": [
            {
              "dataName": "子件编码",
              "dataType": "文本"
            },
            {
              "dataName": "父件编码",
              "dataType": "文本"
            }
          ],
          "gridTabs": [
            {
              "label": "列表",
              "rowSource": "rows",
              "columns": [
                "子件编码",
                "子件名称",
                "规格型号",
                "父件编码",
                "父件名称",
                "版本号",
                "计量单位",
                "定额数量",
                "损耗率%"
              ]
            }
          ],
          "topBarBtn": [
            {
              "buttonName": "刷新"
            }
          ],
          "rowOperationBarBtn": [],
          "events": []
        }
      ],
      "formPages": []
    },
    "panelButtons": [
      {
        "buttonName": "刷新"
      }
    ],
    "buttonGroups": [
      {
        "name": "查找",
        "actions": [
          "查找",
          "刷新"
        ]
      },
      {
        "name": "更多",
        "actions": [
          "导出",
          "退出"
        ]
      }
    ],
    "version": "1.0"
  },
  "dataSchema": {
    "type": "object",
    "fields": []
  },
  "detail": {
    "tabs": []
  }
}
let BOM_REV_ROWS = [
  {
    "编号": "BOMR-001",
    "子件编码": "CL002",
    "子件名称": "6061铝锭",
    "规格型号": "A00",
    "父件编码": "CP001",
    "父件名称": "铝棒 Φ80",
    "版本号": "V1.0",
    "计量单位": "kg",
    "定额数量": 1.05,
    "损耗率%": 5
  },
  {
    "编号": "BOMR-002",
    "子件编码": "CL003",
    "子件名称": "6063铝棒",
    "规格型号": "Φ120",
    "父件编码": "CP003",
    "父件名称": "铝型材-散热片",
    "版本号": "V1.0",
    "计量单位": "kg",
    "定额数量": 1.1,
    "损耗率%": 10
  },
  {
    "编号": "BOMR-003",
    "子件编码": "CL005",
    "子件名称": "包装木箱",
    "规格型号": "1200×800",
    "父件编码": "CP003",
    "父件名称": "铝型材-散热片",
    "版本号": "V1.0",
    "计量单位": "件",
    "定额数量": 0.02,
    "损耗率%": 0
  }
]

// 面板注册表：每张单据一个独立面板（配置 + 数据），页面概念 = 面板独立存在
﻿// ==================== 库存核算报表（16 面板 · 对齐 T+ 实测：ST4001/4002/4004/4005/4006/4008 明细表、ST6001/6002/6004/6011/6012/6014 统计表、ST3030 成本手工维护、ST9001 库存状况表、ST6061 收发存汇总表、ST9002 库存台账） ====================

const REPORT_NUM_RE = /数量|金额|单价|成本|现存量|结存|调整|税额|损耗|重量|换算率|余额/

// T+ 报表面板公共工具栏（查询/打印/导出/发送邮件/刷新/退出）
const REPORT_TOOLBAR = [
  { name: '查询', actions: ['查询', '刷新'] },
  { name: '打印', actions: ['打印', '预览', '导出'] },
  { name: '更多', actions: ['发送邮件', '退出'] },
]

// 成本手工维护（ST3030 实测工具栏：查找/更新单据/设置/批量取成本/汇总…/退出）
const COST_TOOLBAR = [
  { name: '查找', actions: ['查找', '刷新'] },
  { name: '更新单据', actions: ['更新单据'] },
  { name: '设置', actions: ['设置'] },
  { name: '批量取成本', actions: ['批量取成本'] },
  { name: '汇总', actions: ['汇总维度设置', '汇总导出', '汇总导入', '模板下载'] },
  { name: '更多', actions: ['退出'] },
]

// 报表查询条件公共字段（能对应基础档案的一律用参照，开发约束十一-1）
const RPT_Q_WAREHOUSE = { dataName: '仓库', dataType: '参照', refPanel: 'WH', refField: '仓库名称', displayField: '仓库名称' }
const RPT_Q_SUPPLIER = { dataName: '供应商', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称' }
const RPT_Q_CUSTOMER = { dataName: '客户', dataType: '参照', refPanel: 'PARTNER', refField: '往来单位名称', displayField: '往来单位名称' }
const RPT_Q_INV = { dataName: '存货', dataType: '参照', refPanel: 'INV', refField: '存货名称', displayField: '存货名称' }
const RPT_Q_MATERIAL = { dataName: '材料', dataType: '参照', refPanel: 'INV', refField: '存货名称', displayField: '存货名称' }
const RPT_Q_BASE = [
  { dataName: '单据日期', dataType: '日期' },
  { dataName: '单据编号', dataType: '文本' },
  { dataName: '业务类型', dataType: '文本' },
]

// 报表面板配置工厂：只读查询网格（无表单页、无内联新增行，工具栏照 T+）
function reportPanel({ panelCode, panelName, columns, queryFields, toolbar = REPORT_TOOLBAR, category = '报表' }) {
  return {
    metadata: {
      panelCode,
      panelName,
      panelCategory: category,
      readonly: true,
      panelPageDto: {
        tablePages: [
          {
            tableName: panelName,
            queryFields,
            gridTabs: [{ label: '报表', rowSource: 'rows', columns }],
            topBarBtn: [],
            rowOperationBarBtn: [],
            events: [],
          },
        ],
        formPages: [],
      },
      panelButtons: [],
      buttonGroups: toolbar,
      version: '1.0',
    },
    dataSchema: { type: 'object', fields: columns.map((c) => ({ dataName: c, dataType: REPORT_NUM_RE.test(c) ? '小数' : '文本' })) },
    detail: { tabs: [] },
  }
}

// 存货/往来单位字典（从基础档案 seed 提取，报表列引用一致）
const INV_DICT = {}
for (const _r of BASE_CONFIGS.INV.seed) {
  INV_DICT[_r['存货名称']] = { 存货编码: _r['存货编码'], 规格型号: _r['规格型号'], 计量单位: _r['计量单位'], 所属类别: _r['所属类别'], 参考成本: _r['参考成本'] }
}
const PARTNER_DICT = {}
for (const _r of BASE_CONFIGS.PARTNER.seed) PARTNER_DICT[_r['往来单位名称']] = _r['往来单位编码']

const WH_CODE = { 原料仓: '101', 辅料仓: '102', 成品仓: '201', 半成品仓: '202', 不良品仓: '301' }
const PERSON_CODE = { 张伟: '00001', 李娜: '00002', 王强: '00003', 陈强: '00004', 赵工: '00005' }

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100

// 单据明细行 → 报表平铺行（表头 + 明细合并，补齐 T+ 报表列：编码列/部门/制单人/审核人等）
function invReportRows(source, typeName) {
  const out = []
  for (const r of INV_SEED[source] || []) {
    const 仓库 = r['仓库'] || ''
    const head = {
      单据类型: typeName,
      单据日期: r['单据日期'], 创建时间: r['创建时间'], 单据编号: r['单据编号'], 业务类型: r['业务类型'],
      仓库编码: WH_CODE[仓库] || '', 仓库,
      入库类别: r['入库类别'] || '', 出库类别: r['出库类别'] || '',
      供应商编码: r['供应商编码'] || '', 供应商: r['供应商'] || '',
      客户编码: PARTNER_DICT[r['客户']] || '', 客户: r['客户'] || '', 结算客户: r['结算客户'] || '',
      生产车间编码: '', 生产车间: r['生产车间'] || '',
      部门编码: '008', 部门: '仓库',
      经手人编码: PERSON_CODE[r['经手人']] || '', 经手人: r['经手人'] || '',
      领用人编码: PERSON_CODE[r['领用人']] || '', 领用人: r['领用人'] || '',
      备注: '', 制单人: 'tplusdemo12853', 审核人: 'tplusdemo12853',
      销售订单号: r['销售订单号'] || '', 加工单号: r['加工单号'] || '', 匹配来源单号: r['匹配来源单号'] || '',
      往来单位: r['供应商'] || r['客户'] || r['来料客户'] || '', 项目: r['项目'] || '',
    }
    for (const it of r.detail?.items || []) {
      const name = it['存货名称'] || it['产品名称'] || it['材料名称'] || ''
      const info = INV_DICT[name] || {}
      const qty = it['实收数量'] ?? it['数量'] ?? 0
      const price = it['单价'] ?? it['售价'] ?? it['成本价'] ?? 0
      const amt = it['金额'] ?? it['销售金额'] ?? round2(qty * price)
      const 成本价 = it['成本价'] ?? price
      out.push({
        ...head,
        存货编码: it['存货编码'] || info['存货编码'] || '', 存货: name,
        材料编码: it['材料编码'] || info['存货编码'] || '', 材料名称: it['材料名称'] || name, 材料规格: it['规格型号'] || '',
        规格型号: it['规格型号'] || info['规格型号'] || '',
        计量单位: it['计量单位'] || info['计量单位'] || '', 计量单位2: it['计量单位2'] || '',
        主单位: it['计量单位'] || info['计量单位'] || '', 辅单位: it['计量单位2'] || '',
        实收数量: it['实收数量'] ?? 0, 实收数量2: it['实收数量2'] ?? 0,
        数量: qty, 数量2: it['数量2'] ?? 0, 应发数量: qty, 应发数量2: it['数量2'] ?? 0,
        单价: it['单价'] ?? 0, 单价2: it['单价2'] ?? 0,
        金额: amt, 成本价, 成本金额: round2(qty * 成本价),
        入库调整: it['入库调整'] ?? 0, 费用调整: it['费用调整'] ?? 0,
        总成本: round2(amt + (it['入库调整'] ?? 0) + (it['费用调整'] ?? 0)),
        费用金额: it['费用金额'] ?? 0,
        出库调整: it['出库调整'] ?? 0, 累计调拨入库量: 0, 合理损耗数量: 0, 入库单号: '',
        '明细.生产车间': r['生产车间'] || '', 工作中心: it['工作中心'] || '', 班组: it['班组'] || '', 工人: it['工人'] || '', 设备: it['设备'] || '',
        存货分类: info['所属类别'] || '', BOM版本号: '', 参考成本: info['参考成本'] ?? 0,
      })
    }
  }
  return out
}

// 报表分组汇总：按 keys 分组，数值列求和，其余列取首行
function groupSum(rows, keys, sumCols) {
  const map = new Map()
  for (const r of rows) {
    const k = keys.map((x) => r[x] ?? '').join('|')
    let g = map.get(k)
    if (!g) {
      g = {}
      for (const kk of keys) g[kk] = r[kk] ?? ''
      for (const n of sumCols) g[n] = 0
      for (const kk of Object.keys(r)) if (!(kk in g) && !sumCols.includes(kk)) g[kk] = r[kk]
      map.set(k, g)
    }
    for (const n of sumCols) g[n] = round2(g[n] + (r[n] ?? 0))
  }
  return [...map.values()]
}

const avgPrice = (amt, qty) => (qty ? round2(amt / qty) : 0)

// 六类出入库单据的报表平铺行（一次性构建，供明细表/统计表/账表共用）
const INV_FLAT = [
  ...invReportRows('PURCHASE_IN', '采购入库单'),
  ...invReportRows('FINISH_IN', '产成品入库单'),
  ...invReportRows('OTHER_IN', '其他入库单'),
  ...invReportRows('SALE_OUT', '销售出库单'),
  ...invReportRows('MATERIAL_OUT', '材料出库单'),
  ...invReportRows('OTHER_OUT', '其他出库单'),
]

// 收发存汇总基础数据：按 仓库+存货 聚合 期初/本期入库/本期出库/期末（ST6061/ST9001 共用）
const STOCK_INITIAL = { '铝棒 Φ80': 500, '铝板 6061': 400, '铝型材-散热片': 900, '减速箱体 A': 50, '轴套 C': 200, '45#圆钢': 1500, '6061铝锭': 5000, '6063铝棒': 1200, '切削液': 200, '包装木箱': 250 }
const INV_PRIMARY_WH = { 原材料: '原料仓', 辅助材料: '辅料仓', 包装物: '辅料仓', 产成品: '成品仓' }

function stockSummaryRows() {
  const rows = []
  const seen = new Set()
  const add = (wh, inv, 期初数量) => {
    const key = wh + '|' + inv
    if (seen.has(key)) return
    seen.add(key)
    const info = INV_DICT[inv] || {}
    const 期初金额 = round2(期初数量 * (info['参考成本'] || 0))
    const ins = INV_FLAT.filter((r) => r['仓库'] === wh && r['存货'] === inv && ['采购入库单', '产成品入库单', '其他入库单'].includes(r['单据类型']))
    const outs = INV_FLAT.filter((r) => r['仓库'] === wh && r['存货'] === inv && ['销售出库单', '材料出库单', '其他出库单'].includes(r['单据类型']))
    const 入库数量 = round2(ins.reduce((s, r) => s + (r['数量'] ?? r['实收数量'] ?? 0), 0))
    const 出库数量 = round2(outs.reduce((s, r) => s + (r['数量'] ?? 0), 0))
    const 入库金额 = round2(ins.reduce((s, r) => s + (r['金额'] ?? 0), 0))
    const 出库金额 = round2(outs.reduce((s, r) => s + (r['金额'] ?? 0), 0))
    const 期末数量 = round2(期初数量 + 入库数量 - 出库数量)
    const 期末金额 = round2(期初金额 + 入库金额 - 出库金额)
    rows.push({
      仓库编码: WH_CODE[wh] || '', 仓库: wh,
      存货编码: info['存货编码'] || '', 存货: inv, 规格型号: info['规格型号'] || '',
      主单位: info['计量单位'] || '', 辅单位: '',
      期初数量, 期初金额, 期初平均单价: avgPrice(期初金额, 期初数量),
      本期入库数量: 入库数量, 入库平均单价: avgPrice(入库金额, 入库数量), 本期入库金额: 入库金额,
      本期出库数量: 出库数量, 出库平均单价: avgPrice(出库金额, 出库数量), 本期出库金额: 出库金额,
      期末结存数量: 期末数量, 期末平均单价: avgPrice(期末金额, 期末数量), 期末结存金额: 期末金额,
    })
  }
  // 每个存货在其主仓库先落一行（带期初），流转行所在的其它仓库补零期初行
  for (const [name, info] of Object.entries(INV_DICT)) {
    const wh = INV_PRIMARY_WH[info['所属类别']] || '原料仓'
    add(wh, name, STOCK_INITIAL[name] ?? 100)
  }
  for (const r of INV_FLAT) {
    if (r['仓库'] && r['存货']) add(r['仓库'], r['存货'], 0)
  }
  return rows
}

// 库存台账（ST9002）：每存货按时间序 期初 + 收入/发出流水 + 滚动结存
function stockLedgerRows() {
  const rows = []
  const byWhInv = new Map()
  for (const r of stockSummaryRows()) {
    const key = r['仓库'] + '|' + r['存货']
    if (byWhInv.has(key)) continue
    const flows = INV_FLAT
      .filter((f) => f['仓库'] === r['仓库'] && f['存货'] === r['存货'])
      .sort((a, b) => (a['单据日期'] || '').localeCompare(b['单据日期'] || '') || (a['单据编号'] || '').localeCompare(b['单据编号'] || ''))
    byWhInv.set(key, { info: r, flows })
  }
  for (const { info, flows } of byWhInv.values()) {
    const wh = info['仓库'], inv = info['存货']
    let 结存数量 = info['期初数量'], 结存金额 = info['期初金额']
    rows.push({
      单据日期: '', 单据类型: '期初结存', 单据编号: '', 业务类型: '', 往来单位: '', 项目: '',
      收入数量: 0, 收入单价: 0, 收入金额: 0, 发出数量: 0, 发出单价: 0, 发出金额: 0,
      结存数量, 结存平均单价: avgPrice(结存金额, 结存数量), 结存金额,
      仓库: wh, 存货: inv, 规格型号: info['规格型号'], 主计量: info['主单位'],
    })
    for (const f of flows) {
      const isIn = ['采购入库单', '产成品入库单', '其他入库单'].includes(f['单据类型'])
      const qty = isIn ? (f['实收数量'] ?? f['数量'] ?? 0) : (f['数量'] ?? 0)
      const amt = f['金额'] ?? 0
      const unit = isIn ? avgPrice(amt, qty) : f['单价'] ?? 0
      结存数量 = round2(结存数量 + (isIn ? qty : -qty))
      结存金额 = round2(结存金额 + (isIn ? amt : -amt))
      rows.push({
        单据日期: f['单据日期'], 单据类型: f['单据类型'], 单据编号: f['单据编号'], 业务类型: f['业务类型'],
        往来单位: f['往来单位'], 项目: f['项目'],
        收入数量: isIn ? qty : 0, 收入单价: isIn ? unit : 0, 收入金额: isIn ? amt : 0,
        发出数量: isIn ? 0 : qty, 发出单价: isIn ? 0 : unit, 发出金额: isIn ? 0 : amt,
        结存数量, 结存平均单价: avgPrice(结存金额, 结存数量), 结存金额,
        仓库: wh, 存货: inv, 规格型号: info['规格型号'], 主计量: info['主单位'],
      })
    }
  }
  return rows
}

// 16 个报表面板配置（列结构对齐 T+ 实测 report-structure.json）
const REPORT_CONFIGS = {
  PURCHASE_IN_DETAIL: reportPanel({
    panelCode: 'PURCHASE_IN_DETAIL', panelName: '采购入库单明细表',
    columns: ['单据日期', '创建时间', '单据编号', '业务类型', '仓库编码', '仓库', '入库类别', '供应商编码', '供应商', '部门编码', '部门', '经手人编码', '经手人', '备注', '制单人', '审核人', '存货编码', '存货', '规格型号', '计量单位', '实收数量', '单价', '金额', '单价2', '计量单位2', '实收数量2', '入库调整', '费用调整', '总成本', '费用金额'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_SUPPLIER, RPT_Q_INV],
  }),
  FINISH_IN_DETAIL: reportPanel({
    panelCode: 'FINISH_IN_DETAIL', panelName: '产成品入库单明细表',
    columns: ['单据日期', '创建时间', '单据编号', '业务类型', '仓库编码', '仓库', '入库类别', '生产车间编码', '生产车间', '经手人编码', '经手人', '备注', '制单人', '审核人', '存货编码', '存货', '规格型号', '计量单位', '实收数量', '单价', '金额', '计量单位2', '实收数量2'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS }, RPT_Q_INV],
  }),
  OTHER_IN_DETAIL: reportPanel({
    panelCode: 'OTHER_IN_DETAIL', panelName: '其他入库单明细表',
    columns: ['单据日期', '创建时间', '单据编号', '业务类型', '仓库编码', '仓库', '入库类别', '部门编码', '部门', '经手人编码', '经手人', '备注', '制单人', '审核人', '存货编码', '存货', '规格型号', '计量单位', '数量', '单价', '金额', '计量单位2', '数量2'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_INV],
  }),
  SALE_OUT_DETAIL: reportPanel({
    panelCode: 'SALE_OUT_DETAIL', panelName: '销售出库单明细表',
    columns: ['单据日期', '创建时间', '单据编号', '业务类型', '仓库编码', '仓库', '出库类别', '客户编码', '客户', '部门编码', '部门', '经手人编码', '经手人', '制单人', '审核人', '存货编码', '存货', '规格型号', '计量单位', '应发数量', '数量', '计量单位2', '应发数量2', '数量2', '成本价', '成本金额', '出库调整', '销售订单号', '入库单号'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_CUSTOMER, RPT_Q_INV],
  }),
  MATERIAL_OUT_DETAIL: reportPanel({
    panelCode: 'MATERIAL_OUT_DETAIL', panelName: '材料出库单明细表',
    columns: ['单据日期', '创建时间', '单据编号', '业务类型', '仓库编码', '仓库', '出库类别', '生产车间编码', '生产车间', '领用人编码', '领用人', '制单人', '审核人', '材料编码', '材料名称', '材料规格', '明细.生产车间', '工作中心', '班组', '工人', '设备', '计量单位', '数量', '单价', '金额', '计量单位2', '数量2', '出库调整'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS }, RPT_Q_MATERIAL],
  }),
  OTHER_OUT_DETAIL: reportPanel({
    panelCode: 'OTHER_OUT_DETAIL', panelName: '其他出库单明细表',
    columns: ['单据日期', '创建时间', '单据编号', '业务类型', '仓库编码', '仓库', '出库类别', '部门编码', '部门', '经手人编码', '经手人', '备注', '制单人', '审核人', '存货编码', '存货', '规格型号', '计量单位', '数量', '单价', '金额', '计量单位2', '数量2', '出库调整', '累计调拨入库量', '合理损耗数量', '入库单号'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_INV],
  }),
  PURCHASE_IN_STATS: reportPanel({
    panelCode: 'PURCHASE_IN_STATS', panelName: '采购入库单统计表',
    columns: ['仓库编码', '仓库', '供应商编码', '供应商', '存货编码', '存货', '规格型号', '主单位', '辅单位', '实收数量(主单位)', '单价(主单位)', '金额', '单价(辅单位)', '入库调整', '费用调整', '总成本', '费用金额'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_SUPPLIER, RPT_Q_INV],
  }),
  FINISH_IN_STATS: reportPanel({
    panelCode: 'FINISH_IN_STATS', panelName: '产成品入库单统计表',
    columns: ['单据日期', '项目', '存货编码', '存货', '规格型号', '计量单位', '辅单位', '实收数量(主单位)', '单价', '金额', '实收数量(辅单位)', '单价(辅单位)'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS }, RPT_Q_INV],
  }),
  OTHER_IN_STATS: reportPanel({
    panelCode: 'OTHER_IN_STATS', panelName: '其他入库单统计表',
    columns: ['仓库编码', '仓库', '存货编码', '存货', '规格型号', '主单位', '辅单位', '数量(主单位)', '单价', '金额', '数量(辅单位)', '单价(辅单位)'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_INV],
  }),
  SALE_OUT_STATS: reportPanel({
    panelCode: 'SALE_OUT_STATS', panelName: '销售出库单统计表',
    columns: ['单据日期（周）', '存货编码', '存货', '规格型号', '主单位', '辅单位', '数量(主单位)', '成本价(主单位)', '数量(辅单位)', '成本价(辅单位)', '成本金额', '出库调整'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_CUSTOMER, RPT_Q_INV],
  }),
  MATERIAL_OUT_STATS: reportPanel({
    panelCode: 'MATERIAL_OUT_STATS', panelName: '材料出库单统计表',
    columns: ['仓库编码', '仓库', '材料编码', '材料名称', '材料规格', '主单位', '计量单位(辅单位)', '数量(主单位)', '单价(主单位)', '金额', '数量(辅单位)', '单价(辅单位)', '出库调整'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS }, RPT_Q_MATERIAL],
  }),
  OTHER_OUT_STATS: reportPanel({
    panelCode: 'OTHER_OUT_STATS', panelName: '其他出库单统计表',
    columns: ['仓库编码', '仓库', '存货编码', '存货', '规格型号', '主单位', '数量(主单位)', '单价(主单位)', '数量(辅单位)', '单价(辅单位)', '金额', '出库调整', '累计调拨入库量(主单位)', '合理损耗数量(主单位)'],
    queryFields: [...RPT_Q_BASE, RPT_Q_WAREHOUSE, RPT_Q_INV],
  }),
  COST_MAINTAIN: reportPanel({
    panelCode: 'COST_MAINTAIN', panelName: '成本手工维护', category: '查询', toolbar: COST_TOOLBAR,
    columns: ['单据类型', '单据编号', '单据日期', '仓库', '存货编码', '存货分类', '存货', '规格型号', 'BOM版本号', '计量单位', '数量', '成本价', '成本金额', '制单人'],
    queryFields: [RPT_Q_WAREHOUSE, RPT_Q_INV, { dataName: '单据类型', dataType: '文本' }],
  }),
  STOCK_STATUS: reportPanel({
    panelCode: 'STOCK_STATUS', panelName: '库存状况表',
    columns: ['仓库编码', '仓库', '存货编码', '存货', '规格型号', '主计量', '现存量(主)', '结存单价(主)', '结存金额'],
    queryFields: [RPT_Q_WAREHOUSE, RPT_Q_INV],
  }),
  STOCK_SUMMARY: reportPanel({
    panelCode: 'STOCK_SUMMARY', panelName: '收发存汇总表',
    columns: ['仓库编码', '仓库', '存货编码', '存货', '规格型号', '主单位', '辅单位', '期初数量', '期初平均单价', '期初金额', '本期入库数量', '入库平均单价', '本期入库金额', '本期出库数量', '出库平均单价', '本期出库金额', '期末结存数量', '期末平均单价', '期末结存金额'],
    queryFields: [RPT_Q_WAREHOUSE, RPT_Q_INV],
  }),
  STOCK_LEDGER: reportPanel({
    panelCode: 'STOCK_LEDGER', panelName: '库存台账',
    columns: ['单据日期', '单据类型', '单据编号', '业务类型', '往来单位', '项目', '收入数量', '收入单价', '收入金额', '发出数量', '发出单价', '发出金额', '结存数量', '结存平均单价', '结存金额'],
    queryFields: [RPT_Q_WAREHOUSE, RPT_Q_INV],
  }),
}

// 报表行缓存（会话内保存派生结果，保证 mock 下编辑/保存一致）
const REPORT_ROWS_CACHE = {}

function buildReportRows(panelCode) {
  switch (panelCode) {
    case 'PURCHASE_IN_DETAIL': return invReportRows('PURCHASE_IN', '采购入库单')
    case 'FINISH_IN_DETAIL': return invReportRows('FINISH_IN', '产成品入库单')
    case 'OTHER_IN_DETAIL': return invReportRows('OTHER_IN', '其他入库单')
    case 'SALE_OUT_DETAIL': return invReportRows('SALE_OUT', '销售出库单')
    case 'MATERIAL_OUT_DETAIL': return invReportRows('MATERIAL_OUT', '材料出库单')
    case 'OTHER_OUT_DETAIL': return invReportRows('OTHER_OUT', '其他出库单')
    case 'PURCHASE_IN_STATS': {
      const g = groupSum(invReportRows('PURCHASE_IN', '采购入库单'), ['仓库', '供应商', '存货'], ['实收数量', '金额', '入库调整', '费用调整', '总成本', '费用金额'])
      return g.map((r) => ({ ...r, '实收数量(主单位)': r['实收数量'], '单价(主单位)': avgPrice(r['金额'], r['实收数量']), '单价(辅单位)': 0 }))
    }
    case 'FINISH_IN_STATS': {
      const g = groupSum(invReportRows('FINISH_IN', '产成品入库单'), ['存货'], ['实收数量', '金额'])
      return g.map((r) => ({ ...r, '实收数量(主单位)': r['实收数量'], 单价: avgPrice(r['金额'], r['实收数量']), '实收数量(辅单位)': 0, '单价(辅单位)': 0 }))
    }
    case 'OTHER_IN_STATS': {
      const g = groupSum(invReportRows('OTHER_IN', '其他入库单'), ['仓库', '存货'], ['数量', '金额'])
      return g.map((r) => ({ ...r, '数量(主单位)': r['数量'], 单价: avgPrice(r['金额'], r['数量']), '数量(辅单位)': 0, '单价(辅单位)': 0 }))
    }
    case 'SALE_OUT_STATS': {
      const g = groupSum(invReportRows('SALE_OUT', '销售出库单'), ['存货'], ['数量', '成本金额'])
      return g.map((r) => ({ ...r, '单据日期（周）': r['单据日期'], '数量(主单位)': r['数量'], '成本价(主单位)': avgPrice(r['成本金额'], r['数量']), '数量(辅单位)': 0, '成本价(辅单位)': 0 }))
    }
    case 'MATERIAL_OUT_STATS': {
      const g = groupSum(invReportRows('MATERIAL_OUT', '材料出库单'), ['仓库', '材料名称'], ['数量', '金额'])
      return g.map((r) => ({ ...r, '数量(主单位)': r['数量'], '单价(主单位)': avgPrice(r['金额'], r['数量']), '数量(辅单位)': 0, '单价(辅单位)': 0, '计量单位(辅单位)': r['辅单位'] }))
    }
    case 'OTHER_OUT_STATS': {
      const g = groupSum(invReportRows('OTHER_OUT', '其他出库单'), ['仓库', '存货'], ['数量', '金额'])
      return g.map((r) => ({ ...r, '数量(主单位)': r['数量'], '单价(主单位)': avgPrice(r['金额'], r['数量']), '数量(辅单位)': 0, '单价(辅单位)': 0, '累计调拨入库量(主单位)': 0, '合理损耗数量(主单位)': 0 }))
    }
    case 'COST_MAINTAIN':
      return INV_FLAT.map((r) => ({
        单据类型: r['单据类型'], 单据编号: r['单据编号'], 单据日期: r['单据日期'], 仓库: r['仓库'],
        存货编码: r['存货编码'], 存货分类: r['存货分类'], 存货: r['存货'], 规格型号: r['规格型号'],
        BOM版本号: r['BOM版本号'], 计量单位: r['计量单位'], 数量: r['数量'] ?? r['实收数量'] ?? 0,
        成本价: r['成本价'], 成本金额: r['成本金额'], 制单人: r['制单人'],
      }))
    case 'STOCK_STATUS':
      return stockSummaryRows().map((r) => ({
        仓库编码: r['仓库编码'], 仓库: r['仓库'], 存货编码: r['存货编码'], 存货: r['存货'], 规格型号: r['规格型号'],
        主计量: r['主单位'], '现存量(主)': r['期末结存数量'], '结存单价(主)': r['期末平均单价'], 结存金额: r['期末结存金额'],
      }))
    case 'STOCK_SUMMARY': return stockSummaryRows()
    case 'STOCK_LEDGER': return stockLedgerRows()
    default: return []
  }
}

function reportRows(panelCode) {
  if (!REPORT_ROWS_CACHE[panelCode]) REPORT_ROWS_CACHE[panelCode] = buildReportRows(panelCode)
  return REPORT_ROWS_CACHE[panelCode]
}



// ==================== 销售订单 / 生产 / 车间报表（15 面板 · 生产类 12 个配置驱动 + 看板/返修台/方案中心 3 个专属视图；列结构参考 T+ 轻MES 真实菜单与报表体系） ====================

const RPT_Q_WORKSHOP = { dataName: '生产车间', dataType: '下拉框', options: WORKSHOP_OPTIONS }
const RPT_Q_PROC = { dataName: '工序编码', dataType: '参照', refPanel: 'OP', refField: '工序编码', displayField: '工序名称' }
const RPT_Q_WORKER = { dataName: '工人名称', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称' }
const RPT_Q_STATUS = { dataName: '单据状态', dataType: '下拉框', options: ['草稿', '已审核', '生产中', '已完工', '已中止', '已关闭'] }

// 销售订单明细行（报表平铺：表头 + 明细合并）
function soReportRows() {
  const out = []
  for (const r of SO_ROWS) {
    const { detail, ...head } = r
    for (const it of detail?.items || []) {
      out.push({
        单据日期: r['单据日期'], 单据编号: r['单据编号'], 单据状态: r['单据状态'],
        客户编码: r['客户编码'] || '', 客户: r['客户'] || '', 结算客户: r['结算客户'] || '',
        部门: r['部门'] || '', 业务员: r['业务员'] || '', 项目: r['项目'] || '',
        预计交货日期: r['预计交货日期'] || '', 联系人: r['联系人'] || '',
        制单人: 'tplusdemo12855', 审核人: r['审核人'] || '',
        存货编码: it['存货编码'] || '', 存货: it['存货名称'] || '', 规格型号: it['规格型号'] || '',
        计量单位: it['销售单位'] || '件', 品牌: it['存货名称.品牌'] || '',
        数量: it['数量'] ?? 0, 单价: it['单价'] ?? 0, '税率%': it['税率%'] ?? 13,
        含税单价: it['含税单价'] ?? 0, 金额: it['金额'] ?? 0, 含税金额: it['含税金额'] ?? 0,
        折扣金额: it['折扣金额'] ?? 0, 现存量: it['现存量'] ?? 0,
        行预计交货日期: it['预计交货日期'] || r['预计交货日期'] || '',
      })
    }
  }
  return out
}

// 销售订单统计：按 客户+存货 分组汇总
function soStatsRows() {
  const flat = soReportRows()
  const g = groupSum(flat, ['客户', '存货'], ['数量', '金额', '含税金额', '折扣金额'])
  return g.map((r) => ({
    客户编码: r['客户编码'], 客户: r['客户'], 部门: r['部门'], 业务员: r['业务员'],
    存货编码: r['存货编码'], 存货: r['存货'], 规格型号: r['规格型号'], 主单位: r['计量单位'],
    单据数: flat.filter((x) => x['客户'] === r['客户'] && x['存货'] === r['存货']).length,
    '数量(主单位)': r['数量'], 单价: avgPrice(r['金额'], r['数量']),
    金额: r['金额'], 含税金额: r['含税金额'], 折扣金额: r['折扣金额'], 预计交货日期: r['预计交货日期'],
  }))
}

// 生产加工单产成品明细行（表头 + 产成品明细合并）
function manuProductRows() {
  const out = []
  for (const r of MOCK_ROWS) {
    for (const p of r.detail?.products || []) {
      out.push({
        单据编号: r['单据编号'] || r['锭号'] || r['编号'] || '', 单据状态: r['单据状态'], 生产车间: r['生产车间'],
        客户编码: r['客户编码'] || '', 客户: r['客户'] || '', 销售订单号: r['销售订单号'] || '',
        预开工日: r['预开工日'] || '', 预完工日: r['预完工日'] || '',
        开工日期: r['开工日期'] || '', 完工日期: r['完工日期'] || '',
        负责人: r['负责人'] || '', 批号: r['批号'] || '', 机构: r['机构'] || '',
        ...p,
      })
    }
  }
  return out
}

// 生产加工单工序行（表头 + 工序明细合并，产品取单首产品）
function manuProcessRows() {
  const out = []
  for (const r of MOCK_ROWS) {
    const prod = r.detail?.products?.[0] || {}
    for (const c of r.detail?.processes || []) {
      out.push({
        单据编号: r['单据编号'] || r['锭号'] || r['编号'] || '', 单据状态: r['单据状态'], 生产车间: r['生产车间'],
        客户: r['客户'] || '', 销售订单号: r['销售订单号'] || '',
        预开工日: r['预开工日'] || '', 预完工日: r['预完工日'] || '',
        产品编码: prod['产品编码'] || '', 产品名称: prod['产品名称'] || '', 规格型号: prod['规格型号'] || '',
        负责人: r['负责人'] || '', 批号: r['批号'] || '',
        ...c,
      })
    }
  }
  return out
}

// 生产加工单统计：按 产品 分组
function manuStatsRows() {
  const flat = manuProductRows()
  const g = groupSum(flat, ['产品编码', '产品名称', '规格型号'], ['数量', '累计汇报套数(工序单位)'])
  return g.map((r) => {
    const rows = flat.filter((x) => x['产品编码'] === r['产品编码'])
    const plan = r['数量'] || 0
    return {
      产品编码: r['产品编码'], 产品名称: r['产品名称'], 规格型号: r['规格型号'],
      生产单位: r['生产单位'] || '件', 加工单数: rows.length, 计划数量: plan,
      累计汇报数量: r['累计汇报套数(工序单位)'],
      完工数量: round2(rows.filter((x) => x['单据状态'] === '已完工').reduce((s, x) => s + (x['数量'] ?? 0), 0)),
      生产进度: plan ? round2((r['累计汇报套数(工序单位)'] / plan) * 100) : 0,
    }
  })
}

// 生产加工单工序统计：按 工序 分组
function manuProcStatsRows() {
  const flat = manuProcessRows()
  const g = groupSum(flat, ['工序编码', '工序名称', '生产车间'], ['计划数量', '金额'])
  return g.map((r) => {
    const rows = flat.filter((x) => x['工序编码'] === r['工序编码'] && x['工序名称'] === r['工序名称'] && x['生产车间'] === r['生产车间'])
    const st = rows.reduce((s, x) => { const k = x['工序完工状态'] || '未开工'; s[k] = (s[k] ?? 0) + 1; return s }, {})
    return {
      工序编码: r['工序编码'], 工序名称: r['工序名称'], 生产车间: r['生产车间'],
      工作中心: r['工作中心'] || '', 班组: r['班组'] || '', 设备: r['设备'] || '',
      加工单数: rows.length, 计划数量: r['计划数量'], 金额: r['金额'],
      已完工: st['已完工'] ?? 0, 进行中: st['进行中'] ?? 0, 未开工: st['未开工'] ?? 0,
      单位标准工时: r['单位标准工时'] ?? 0,
    }
  })
}

// 工序汇报明细行（工序汇报单明细表/工资明细表共用）
function procDetailRows() {
  return flattenProcessReportRows().map((r) => ({
    单据编号: r['单据编号'], 单据状态: r['单据状态'], 单据日期: r['单据日期'],
    加工单号: r['加工单号'] || '', 生产车间: r['生产车间'] || '', 产品编码: r['产品编码'] || '', 产品名称: r['产品名称'] || '',
    规格型号: r['规格型号'] || '', 客户: r['客户'] || '', 销售订单号: r['销售订单号'] || '',
    工序编码: r['工序编码'], 工序名称: r['工序名称'], 班组名称: r['班组名称'] || '', 工人名称: r['工人名称'] || '',
    工序单位: r['工序单位'] || '件', 报工数量: r['报工数量'] ?? 0, 合格数量: r['合格数量'] ?? 0,
    不合格数量: r['不合格数量'] ?? 0, 工资类型: r['工资类型'] || '计件', 工价: r['工价'] ?? 0,
    '计时/计件金额': r['计时/计件金额'] ?? 0, 金额: r['金额'] ?? 0,
    制单人: r['制单人'] || '', 审核人: r['审核人'] || '',
  }))
}

// 工序统计：按 工序+班组+工人 分组
function procStatsRows() {
  const flat = procDetailRows()
  const g = groupSum(flat, ['工序编码', '工序名称', '班组名称', '工人名称'], ['报工数量', '合格数量', '不合格数量', '金额'])
  return g.map((r) => ({
    工序编码: r['工序编码'], 工序名称: r['工序名称'], 生产车间: r['生产车间'],
    班组名称: r['班组名称'], 工人名称: r['工人名称'],
    报工单数: flat.filter((x) => x['工序编码'] === r['工序编码'] && x['工人名称'] === r['工人名称']).length,
    报工数量: r['报工数量'], 合格数量: r['合格数量'], 不合格数量: r['不合格数量'],
    合格率: r['报工数量'] ? round2((r['合格数量'] / r['报工数量']) * 100) : 0,
    金额: r['金额'],
  }))
}

// 工资明细（按 汇报行 计件工资）
function salaryDetailRows() {
  return procDetailRows().map((r) => ({
    单据编号: r['单据编号'], 单据日期: r['单据日期'], 单据状态: r['单据状态'],
    加工单号: r['加工单号'], 工序编码: r['工序编码'], 工序名称: r['工序名称'],
    班组名称: r['班组名称'], 工人名称: r['工人名称'], 工资类型: r['工资类型'],
    计件数量: r['合格数量'], 工价: r['工价'],
    计件金额: r['计时/计件金额'], 金额: r['金额'],
  }))
}

// 工资统计：按 工人+班组 分组
function salaryStatsRows() {
  const flat = salaryDetailRows()
  const g = groupSum(flat, ['工人名称', '班组名称'], ['计件数量', '计件金额', '金额'])
  return g.map((r) => ({
    工人名称: r['工人名称'], 班组名称: r['班组名称'],
    工资类型: r['工资类型'], 单据数: flat.filter((x) => x['工人名称'] === r['工人名称'] && x['班组名称'] === r['班组名称']).length,
    计件数量: r['计件数量'], 计件金额: r['计件金额'], 金额合计: r['金额'],
  }))
}

// 返修工序汇报单：生产加工单工序含 待返修数量 的行（本序/他序）
function reworkReportRows() {
  const out = []
  for (const p of manuProcessRows()) {
    const 本序 = p['待返修数量-本序发现'] ?? 0
    const 他序 = p['待返修数量-他序发现'] ?? 0
    if (!本序 && !他序) continue
    out.push({
      单据编号: p['单据编号'], 单据日期: p['预开工日'] || '', 单据状态: p['单据状态'],
      加工单号: p['单据编号'], 生产车间: p['生产车间'],
      产品编码: p['产品编码'], 产品名称: p['产品名称'], 规格型号: p['规格型号'], 客户: p['客户'],
      工序编码: p['工序编码'], 工序名称: p['工序名称'], 工作中心: p['工作中心'], 设备: p['设备'],
      班组: p['班组'], 工人: p['工人'],
      '待返修数量-本序发现': 本序, '待返修数量-他序发现': 他序, 待返修合计: round2(本序 + 他序),
      返修责任工序: 他序 ? p['工序名称'] : '', 返修转出目的工序: '',
      返修状态: '待返修',
    })
  }
  return out
}

// 12 个生产/销售报表面板（列结构参考 T+ 轻MES 报表体系：产成品明细表/统计表/工序统计表/执行表/工序汇报单明细表/统计表/工资核算单明细表/统计表）
const PROD_REPORT_CONFIGS = {
  SALES_ORDER_DETAIL: reportPanel({
    panelCode: 'SALES_ORDER_DETAIL', panelName: '销售订单明细表',
    columns: ['单据日期', '单据编号', '单据状态', '客户编码', '客户', '结算客户', '部门', '业务员', '项目', '存货编码', '存货', '规格型号', '计量单位', '数量', '单价', '税率%', '含税单价', '金额', '含税金额', '折扣金额', '预计交货日期', '现存量', '制单人', '审核人'],
    queryFields: [RPT_Q_BASE[0], RPT_Q_BASE[1], RPT_Q_STATUS, RPT_Q_CUSTOMER, RPT_Q_INV, { dataName: '业务员', dataType: '参照', refPanel: 'EMP', refField: '员工名称', displayField: '员工名称' }],
  }),
  SALES_ORDER_STATS: reportPanel({
    panelCode: 'SALES_ORDER_STATS', panelName: '销售订单统计表',
    columns: ['客户编码', '客户', '部门', '业务员', '存货编码', '存货', '规格型号', '主单位', '单据数', '数量(主单位)', '单价', '金额', '含税金额', '折扣金额', '预计交货日期'],
    queryFields: [RPT_Q_BASE[0], RPT_Q_BASE[1], RPT_Q_CUSTOMER, RPT_Q_INV],
  }),
  SALES_ORDER_EXEC: reportPanel({
    panelCode: 'SALES_ORDER_EXEC', panelName: '销售订单执行表',
    columns: ['单据编号', '单据日期', '客户编码', '客户', '部门', '业务员', '存货编码', '存货', '规格型号', '订单数量', '已出库数量', '出库执行率%', '已生产数量', '生产进度%', '未执行数量', '预计交货日期', '单据状态'],
    queryFields: [RPT_Q_BASE[1], RPT_Q_BASE[0], RPT_Q_CUSTOMER, RPT_Q_INV, RPT_Q_STATUS],
  }),
  SALES_ORDER_PROGRESS: reportPanel({
    panelCode: 'SALES_ORDER_PROGRESS', panelName: '销售订单生产进度表',
    columns: ['单据编号', '单据日期', '客户', '存货编码', '存货', '规格型号', '订单数量', '加工单号', '加工单数量', '已汇报数量', '完工数量', '生产进度%', '预完工日', '单据状态'],
    queryFields: [RPT_Q_BASE[1], RPT_Q_BASE[0], RPT_Q_CUSTOMER, RPT_Q_INV, RPT_Q_STATUS],
  }),
  MANU_ORDER_DETAIL: reportPanel({
    panelCode: 'MANU_ORDER_DETAIL', panelName: '生产加工单明细表',
    columns: ['单据编号', '单据状态', '生产车间', '客户编码', '客户', '产品编码', '产品名称', '规格型号', '生产单位', '数量', '齐套数量(主)', '累计汇报套数(工序单位)', '可用量', '现存量', '图号', '单重', '总重', '需求令号', '预开工日', '预完工日'],
    queryFields: [RPT_Q_BASE[1], RPT_Q_STATUS, RPT_Q_WORKSHOP, RPT_Q_CUSTOMER, RPT_Q_INV],
  }),
  MANU_ORDER_STATS: reportPanel({
    panelCode: 'MANU_ORDER_STATS', panelName: '生产加工单统计表',
    columns: ['产品编码', '产品名称', '规格型号', '生产单位', '加工单数', '计划数量', '累计汇报数量', '完工数量', '生产进度%'],
    queryFields: [RPT_Q_BASE[1], RPT_Q_WORKSHOP, RPT_Q_CUSTOMER, RPT_Q_INV],
  }),
  MANU_PROC_STATS: reportPanel({
    panelCode: 'MANU_PROC_STATS', panelName: '生产加工单工序统计表',
    columns: ['工序编码', '工序名称', '生产车间', '工作中心', '班组', '设备', '加工单数', '计划数量', '金额', '已完工', '进行中', '未开工', '单位标准工时'],
    queryFields: [RPT_Q_BASE[1], RPT_Q_WORKSHOP, RPT_Q_PROC, RPT_Q_STATUS],
  }),
  PROC_DETAIL: reportPanel({
    panelCode: 'PROC_DETAIL', panelName: '工序明细表',
    columns: ['单据编号', '单据状态', '单据日期', '加工单号', '生产车间', '产品编码', '产品名称', '规格型号', '工序编码', '工序名称', '班组名称', '工人名称', '工序单位', '报工数量', '合格数量', '不合格数量', '工资类型', '工价', '计时/计件金额', '金额', '客户', '制单人', '审核人'],
    queryFields: [RPT_Q_BASE[1], RPT_Q_BASE[0], RPT_Q_STATUS, RPT_Q_WORKSHOP, RPT_Q_PROC, RPT_Q_WORKER],
  }),
  PROC_STATS: reportPanel({
    panelCode: 'PROC_STATS', panelName: '工序统计表',
    columns: ['工序编码', '工序名称', '生产车间', '班组名称', '工人名称', '报工单数', '报工数量', '合格数量', '不合格数量', '合格率%', '金额'],
    queryFields: [RPT_Q_BASE[0], RPT_Q_STATUS, RPT_Q_PROC, RPT_Q_WORKER],
  }),
  SALARY_DETAIL: reportPanel({
    panelCode: 'SALARY_DETAIL', panelName: '工资明细表',
    columns: ['单据编号', '单据日期', '单据状态', '加工单号', '工序编码', '工序名称', '班组名称', '工人名称', '工资类型', '计件数量', '工价', '计件金额', '金额'],
    queryFields: [RPT_Q_BASE[0], RPT_Q_BASE[1], RPT_Q_WORKER, { dataName: '班组名称', dataType: '文本' }],
  }),
  SALARY_STATS: reportPanel({
    panelCode: 'SALARY_STATS', panelName: '工资统计表',
    columns: ['工人名称', '班组名称', '工资类型', '单据数', '计件数量', '计件金额', '金额合计'],
    queryFields: [RPT_Q_BASE[0], RPT_Q_WORKER, { dataName: '班组名称', dataType: '文本' }],
  }),
  REWORK_REPORT: reportPanel({
    panelCode: 'REWORK_REPORT', panelName: '返修工序汇报单', category: '单据',
    columns: ['单据编号', '单据日期', '单据状态', '加工单号', '产品编码', '产品名称', '规格型号', '客户', '工序编码', '工序名称', '工作中心', '设备', '班组', '工人', '待返修数量-本序发现', '待返修数量-他序发现', '待返修合计', '返修责任工序', '返修状态'],
    queryFields: [RPT_Q_BASE[1], RPT_Q_STATUS, RPT_Q_WORKSHOP, RPT_Q_PROC],
  }),
}
Object.assign(REPORT_CONFIGS, PROD_REPORT_CONFIGS)

// 生产/销售报表行构建器：模块加载时预生成并入缓存（与库存报表同一缓存，返修工作台可联动修改）
const PROD_REPORT_BUILDERS = {
  SALES_ORDER_DETAIL: soReportRows,
  SALES_ORDER_STATS: soStatsRows,
  SALES_ORDER_EXEC: soExecRows,
  SALES_ORDER_PROGRESS: soProgressRows,
  MANU_ORDER_DETAIL: manuProductRows,
  MANU_ORDER_STATS: manuStatsRows,
  MANU_PROC_STATS: manuProcStatsRows,
  PROC_DETAIL: procDetailRows,
  PROC_STATS: procStatsRows,
  SALARY_DETAIL: salaryDetailRows,
  SALARY_STATS: salaryStatsRows,
  REWORK_REPORT: reworkReportRows,
}
for (const [code, fn] of Object.entries(PROD_REPORT_BUILDERS)) REPORT_ROWS_CACHE[code] = fn()

// 销售订单执行表：订单数量 vs 已出库（SALE_OUT 按 销售订单号+存货 匹配）/ 已生产（MANU 按 销售订单号+产品 匹配）
function soExecRows() {
  const out = []
  for (const r of soReportRows()) {
    const soNo = r['单据编号']
    const outs = INV_FLAT.filter((f) => f['单据类型'] === '销售出库单' && f['销售订单号'] === soNo && f['存货'] === r['存货'])
    const 已出库数量 = round2(outs.reduce((s, f) => s + (f['数量'] ?? 0), 0))
    const mos = manuProductRows().filter((m) => m['销售订单号'] === soNo && m['产品名称'] === r['存货'])
    const 已生产数量 = round2(mos.reduce((s, m) => s + (m['数量'] ?? 0), 0))
    const 汇报数量 = round2(mos.reduce((s, m) => s + (m['累计汇报套数(工序单位)'] ?? 0), 0))
    out.push({
      ...r,
      订单数量: r['数量'], 已出库数量,
      出库执行率: r['数量'] ? round2((已出库数量 / r['数量']) * 100) : 0,
      已生产数量,
      生产进度: r['数量'] ? round2((汇报数量 / r['数量']) * 100) : 0,
      未执行数量: round2(r['数量'] - 已出库数量),
    })
  }
  return out
}

// 销售订单生产进度表：订单 → 加工单 → 汇报 联动
function soProgressRows() {
  const out = []
  for (const r of soReportRows()) {
    const mos = manuProductRows().filter((m) => m['销售订单号'] === r['单据编号'] && m['产品名称'] === r['存货'])
    out.push({
      ...r,
      订单数量: r['数量'],
      加工单号: mos.map((m) => m['单据编号']).join('、'),
      加工单数量: round2(mos.reduce((s, m) => s + (m['数量'] ?? 0), 0)),
      已汇报数量: round2(mos.reduce((s, m) => s + (m['累计汇报套数(工序单位)'] ?? 0), 0)),
      完工数量: round2(mos.filter((m) => m['单据状态'] === '已完工').reduce((s, m) => s + (m['数量'] ?? 0), 0)),
      生产进度: r['数量'] ? round2((mos.reduce((s, m) => s + (m['累计汇报套数(工序单位)'] ?? 0), 0) / r['数量']) * 100) : 0,
      预完工日: mos[0]?.['预完工日'] || '',
    })
  }
  return out
}

// ==================== 专属视图数据（生产看板 / 返修工作台，mock 派生） ====================

export function getProdBoard() {
  if (!USE_MOCK) return null
  const orders = []
  let planQty = 0, reportQty = 0, reworkTotal = 0
  const wsMap = {}
  for (const r of MOCK_ROWS) {
    const products = r.detail?.products || []
    const plan = round2(products.reduce((s, p) => s + (p['数量'] ?? 0), 0))
    const report = round2(products.reduce((s, p) => s + (p['累计汇报套数(工序单位)'] ?? 0), 0))
    planQty += plan
    reportQty += report
    const st = { 已完工: 0, 进行中: 0, 未开工: 0 }
    for (const c of r.detail?.processes || []) {
      const k = c['工序完工状态'] || '未开工'
      st[k] = (st[k] ?? 0) + 1
      reworkTotal += (c['待返修数量-本序发现'] ?? 0) + (c['待返修数量-他序发现'] ?? 0)
    }
    const ws = r['生产车间'] || '未分配'
    if (!wsMap[ws]) wsMap[ws] = { 车间: ws, 已完工: 0, 进行中: 0, 未开工: 0, 计划数量: 0 }
    wsMap[ws]['计划数量'] += plan
    for (const k of ['已完工', '进行中', '未开工']) wsMap[ws][k] += st[k] || 0
    orders.push({
      单据编号: r['单据编号'], 单据状态: r['单据状态'], 生产车间: ws, 客户: r['客户'] || '',
      产品名称: products[0]?.['产品名称'] || '', 数量: plan, 已汇报: report,
      进度: plan ? round2((report / plan) * 100) : 0,
      预完工日: r['预完工日'] || '', 工序状态: st,
    })
  }
  return {
    kpis: {
      在制单数: MOCK_ROWS.length, 计划数量: planQty, 已汇报数量: reportQty,
      平均进度: planQty ? round2((reportQty / planQty) * 100) : 0, 待返修数量: reworkTotal,
    },
    orders,
    workshops: Object.values(wsMap),
  }
}

export function getReworkTasks() {
  if (!USE_MOCK) return []
  return reportRows('REWORK_REPORT').map((r) => ({ ...r }))
}

export function reworkAction(row, action) {
  if (!USE_MOCK) return false
  const target = reportRows('REWORK_REPORT').find((r) => r['加工单号'] === row['加工单号'] && r['工序编码'] === row['工序编码'])
  if (!target) return false
  if (action === '开始返修') target['返修状态'] = '返修中'
  if (action === '完成返修') target['返修状态'] = '已返修'
  return true
}

// ==================== 审批流（对齐 T+ 实测：HasAuditProcess=True 的 15 面板；mock 模拟 提交审批→审批中→审批通过/驳回） ====================
const APPROVAL_PANELS = new Set([
  'SO_ORDER', 'PURCHASE_IN', 'FINISH_IN', 'OTHER_IN', 'SALE_OUT', 'MATERIAL_OUT', 'OTHER_OUT',
  'MANU_ORDER', 'PROCESS_REPORT', 'INIT_AP', 'INIT_AR', 'INIT_BALANCE', 'BOM', 'ROUTE',
])
const APPROVAL_ACTIONS = ['提交审批', '审批通过', '驳回审批']

function applyApprovalConfig(cfg, panelCode) {
  if (!cfg || !APPROVAL_PANELS.has(panelCode)) return cfg
  const md = cfg.metadata
  // 单据状态选项补「审批中」
  if (md.panelState) {
    const opts = md.panelState.defaultOptions || []
    if (!opts.includes('审批中')) {
      const ai = opts.indexOf('已审核')
      opts.splice(ai >= 0 ? ai + 1 : opts.length, 0, '审批中')
    }
  }
  // 工具栏补「审批」分组（插在 审核 组之后）
  const groups = md.buttonGroups || []
  if (!groups.some((g) => g.name === '审批')) {
    const group = { name: '审批', actions: [...APPROVAL_ACTIONS] }
    const gi = groups.findIndex((g) => g.name === '审核')
    if (gi >= 0) groups.splice(gi + 1, 0, group)
    else groups.push(group)
  }
  for (const a of APPROVAL_ACTIONS) {
    if (!(md.panelButtons || []).some((b) => b.buttonName === a)) md.panelButtons.push({ buttonName: a })
  }
  return cfg
}

function panelOf(panelCode) {
  if (REPORT_CONFIGS[panelCode]) return { config: REPORT_CONFIGS[panelCode], rows: reportRows(panelCode) }
  if (BASE_CONFIGS[panelCode]) return { config: BASE_CONFIGS[panelCode].config, rows: BASE_CONFIGS[panelCode].seed }
  if (panelCode === 'ROUTE') return { config: ROUTE_CONFIG, rows: ROUTE_ROWS }
  if (panelCode === 'BOM') return { config: BOM_CONFIG, rows: BOM_ROWS }
  if (panelCode === 'BOM_FWD') return { config: BOM_FWD_CONFIG, rows: BOM_FWD_ROWS }
  if (panelCode === 'BOM_REV') return { config: BOM_REV_CONFIG, rows: BOM_REV_ROWS }
  if (INV_CONFIGS[panelCode]) return { config: INV_CONFIGS[panelCode], rows: INV_SEED[panelCode] || [] }
  if (panelCode === 'PROCESS_REPORT') return { config: PROCESS_REPORT_CONFIG, rows: PROCESS_REPORT_ROWS }
  return panelCode === 'SO_ORDER' ? { config: SO_CONFIG, rows: SO_ROWS } : { config: MOCK_CONFIG, rows: MOCK_ROWS }
}

function buildMeta(cfg = MOCK_CONFIG) {
  return cfg.dataSchema.fields.map((f) => ({
    code: f.dataName,
    name: f.displayName || f.dataName,
    dataType: f.dataType,
    isNotNull: !!f.isRequired,
    defaultValue: f.defaultValue,
    options: f.dataType === '参照' ? (resolveRefOptions(f) || []).map((o) => o.value) : f.options,
    displayOptions: f.dataType === '参照' ? resolveRefOptions(f) : undefined,
    autoCode: !!f.autoCode,
    // 参照字段信息（开发约束十一-1：能对应基础档案的字段必须引用，弹窗拉取面板数据勾选导入）
    ref:
      f.dataType === '参照'
        ? {
            panel: f.refPanel,
            field: f.refField,
            display: f.displayField,
            filter: f.filter || undefined,
            map: f.refMap || undefined,
            multi: !!f.refMulti,
            columns: f.refColumns || undefined,
          }
        : undefined,
  }))
}

function actionPrivileges(cfg = MOCK_CONFIG) {
  return cfg.metadata.panelButtons.map((b) => ({ name: b.buttonName, visible: true, operatable: true }))
}

function privilege(cfg = MOCK_CONFIG) {
  return { actionPrivileges: actionPrivileges(cfg), fieldPrivileges: [], groupPrivileges: [] }
}

// 引用字段（参照）选项解析：从基础档案面板 seed 动态提取（开发约束十一-1，禁止手写静态 options）
// 字段约定：{ dataType: '参照', refPanel: 'DEPT', refField: '部门编码', displayField: '部门名称', filter: {...} }
export function resolveRefOptions(field) {
  if (!field || field.dataType !== '参照' || !field.refPanel) return null
  const p = panelOf(field.refPanel)
  const rows = (p && p.rows) || []
  const display = field.displayField || field.refField
  const seen = new Set()
  const out = []
  for (const r of rows) {
    const fv = r[field.refField]
    if (fv === undefined || fv === null || fv === '') continue
    if (field.filter) {
      let ok = true
      for (const [k, v] of Object.entries(field.filter)) {
        if (String(r[k]) !== String(v)) { ok = false; break }
      }
      if (!ok) continue
    }
    const key = String(fv)
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ value: fv, label: r[display] !== undefined && r[display] !== '' ? r[display] : fv })
  }
  return out
}

// 字段选项统一解析：普通下拉返回原 options；参照字段返回动态选项（对象 {value,label} 数组或字符串数组）
export function fieldOptions(field) {
  const ref = resolveRefOptions(field)
  if (ref) return ref
  return field.options || []
}

// ==================== 参照字段：弹窗拉取面板数据（开发约束十一-1） ====================
// 字段约定：{ dataType: '参照', refPanel, refField, displayField, filter, refMap, refMulti, refColumns }
// 交互：点击参照字段 → 弹窗展示 refPanel 面板数据列表 → 勾选行 → 确定导入（值写 refField，refMap 带出其他字段）
// 兼容两种字段形态：原始配置字段（refPanel/refField/...）与 buildMeta 输出的 meta.ref（panel/field/display/...）

function normRef(r) {
  if (!r) return {}
  // meta 字段的参照信息嵌套在 f.ref（buildMeta 输出 {panel,field,display,filter,map,multi,columns}），
  // 原始配置字段直接平铺（refPanel/refField/...），两者归一
  const src = r.ref && typeof r.ref === 'object' ? r.ref : r
  return {
    dataType: '参照',
    refPanel: src.panel || src.refPanel,
    refField: src.field || src.refField,
    displayField: src.display || src.displayField,
    filter: src.filter,
    refMap: src.map || src.refMap,
    refMulti: src.multi || src.refMulti,
    refColumns: src.columns || src.refColumns,
  }
}

// 引用面板名称（弹窗标题）
export function refPanelName(field) {
  const r = normRef(field)
  try {
    const p = panelOf(r.refPanel)
    return (p && p.config && p.config.metadata && p.config.metadata.panelName) || r.refPanel
  } catch (e) {
    return r.refPanel
  }
}

// 弹窗表格列：优先字段 refColumns，其次引用面板网格列，最后 refField/displayField
export async function refColumns(field) {
  const r = normRef(field)
  if (r.refColumns && r.refColumns.length) return r.refColumns
  let cols = null
  try {
    const cfg = USE_MOCK ? panelOf(r.refPanel)?.config : await getPanelConfig(r.refPanel)
    cols = cfg?.metadata?.panelPageDto?.tablePages?.[0]?.gridTabs?.[0]?.columns
  } catch (e) {
    /* 平台模式/后端无该面板时走兜底列 */
  }
  if (cols && cols.length) return cols
  return [...new Set([r.refField, r.displayField].filter(Boolean))]
}

// 拉取引用面板数据（mock：本地 seed + filter 精确 + keyword 模糊；真实：queryFormDataList 走后端/平台）
export async function queryRefRows(field, { keyword = '', pageSize = 200 } = {}) {
  const r = normRef(field)
  if (USE_MOCK) {
    await mockDelay(80)
    let rows = panelOf(r.refPanel)?.rows || []
    if (r.filter) {
      rows = rows.filter((row) => Object.entries(r.filter).every(([k, v]) => String(row[k]) === String(v)))
    }
    if (keyword) {
      const k = String(keyword).toLowerCase()
      rows = rows.filter((row) => Object.values(row).some((v) => String(v ?? '').toLowerCase().includes(k)))
    }
    return rows.slice(0, pageSize)
  }
  const res = await queryFormDataList({ panelCode: r.refPanel, condition: r.filter || {}, keyword, pageNo: 1, pageSize })
  return res.list || []
}

// 参照显示文本：mock 下从 refPanel seed 解析（返回 label）；真实模式下返回 null（调用方回退显示原值）
export function refLabelOf(field, value) {
  if (value === undefined || value === null || value === '') return ''
  if (!USE_MOCK) return null
  const r = normRef(field)
  const opts = resolveRefOptions(r) || []
  const found = opts.find((o) => String(o.value) === String(value))
  return found ? found.label : String(value)
}

// 面板自动单号（MO：锭号=MO-yyyy-MM-####；SO：单据编号=SO-yyyy-MM-####；库存：RK/CP/IC/IO/MD/ID-yyyy-MM-####）
function nextNoFor(panelCode) {
  if (BASE_CONFIGS[panelCode]) {
    const prefix = panelCode + '-'
    const rows = BASE_CONFIGS[panelCode].seed
    const nums = rows.map((r) => r['编号'] || '').filter((s) => s.startsWith(prefix)).map((s) => Number(s.slice(prefix.length))).filter((n) => !Number.isNaN(n))
    return prefix + String((nums.length ? Math.max(...nums) : 0) + 1).padStart(3, '0')
  }
  if (INV_CONFIGS[panelCode]) return nextInvNo(panelCode)
  if (panelCode === 'PROCESS_REPORT') {
    const prefix = 'MR-' + today + '-'
    const nums = PROCESS_REPORT_ROWS.map((r) => r['编号'] || '').filter((s) => s.startsWith(prefix)).map((s) => Number(s.slice(prefix.length))).filter((n) => !Number.isNaN(n))
    return prefix + String((nums.length ? Math.max(...nums) : 0) + 1).padStart(4, '0')
  }
  return panelCode === 'SO_ORDER' ? nextSoNo() : nextIngotNo()
}

// 面板列表展平（MO：产成品明细行；SO：明细行；库存：明细行）
function flattenFor(panelCode) {
  if (REPORT_CONFIGS[panelCode]) return panelOf(panelCode).rows
  if (BASE_CONFIGS[panelCode] || panelCode === 'ROUTE' || panelCode === 'BOM' || panelCode === 'BOM_FWD' || panelCode === 'BOM_REV') {
    return panelOf(panelCode).rows
  }
  if (INV_CONFIGS[panelCode]) return flattenInvRows(panelCode)
  if (panelCode === 'PROCESS_REPORT') return flattenProcessReportRows()
  return panelCode === 'SO_ORDER' ? flattenSoRows() : flattenRows()
}

function nextIngotNo() {
  const prefix = `MO-${today}-`
  const nums = MOCK_ROWS.map((r) => r['锭号'] || '').filter((s) => s.startsWith(prefix)).map((s) => Number(s.slice(prefix.length))).filter((n) => !Number.isNaN(n))
  return prefix + String((nums.length ? Math.max(...nums) : 0) + 1).padStart(4, '0')
}

// 列表展平：产成品明细行（对齐 T+ 真实列表视图）
function flattenRows() {
  const out = []
  for (const r of MOCK_ROWS) {
    const { detail, ...head } = r
    for (const p of detail?.products || []) {
      out.push({ ...head, ...p, 子表数量: 1 })
    }
  }
  out.sort((a, b) => (a['编号'] < b['编号'] ? 1 : -1))
  return out
}

// ==================== 接口（mock / 真实 双模式） ====================

export async function getPanelConfig(panelCode) {
  if (USE_MOCK) {
    await mockDelay()
    return applyApprovalConfig(JSON.parse(JSON.stringify(panelOf(panelCode).config)), panelCode)
  }
  if (USE_PANELX) return platformConfig(panelCode)
  return unwrap(await request.get('/px/getPanelConfig', { params: { panelCode } }))
}

export async function getPermMatrix(panelCode) {
  if (USE_MOCK) {
    await mockDelay()
    return { privilege: privilege() }
  }
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    return unwrap(await platformCall((sd) => sd.api.getPermMatrix({ panelCode: resolvePanelCode(panelCode) })))
  }
  return unwrap(await request.get('/px/getPermMatrix', { params: { panelCode } }))
}

export async function getNewFormPermMatrix({ panelCode, operationName }) {
  if (USE_MOCK) {
    await mockDelay()
    const p = panelOf(panelCode)
    const data = { 单据状态: '草稿' }
    for (const f of p.config.dataSchema.fields) {
      if (f.defaultValue !== undefined && f.defaultValue !== '') data[f.dataName] = f.defaultValue
    }
    const _af = p.config.metadata.autoCodeField
    if (_af) data[_af] = nextNoFor(panelCode)
    else data['编号'] = ''
    data['编号'] = ''
    return { data, meta: buildMeta(p.config), privilege: privilege(p.config), detail: p.config.detail, buttonGroups: p.config.metadata.buttonGroups, selectConfig: p.config.selectConfig }
  }
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const p = unwrap(await platformCall((sd) => sd.api.getNewFormPermMatrix({ panelCode: resolvePanelCode(panelCode), operationName })))
    const cfg = await platformConfig(panelCode)
    const fp = cfg.metadata.panelPageDto.formPages?.[0] || {}
    return {
      data: adaptFormData(p.data),
      meta: adaptMeta(p.meta, p.dataSchema, fp.fieldNames),
      privilege: p.privilege,
      detail: { tabs: [] },
      buttonGroups: panelxButtonGroups(fp.bottomOperationBarBtn, ['新增流程']),
    }
  }
  return unwrap(await request.get('/px/getNewFormPermMatrix', { params: { panelCode, operationName } }))
}

export async function getFormDescriptor({ panelCode, code }) {
  if (USE_MOCK) {
    await mockDelay()
    const p = panelOf(panelCode)
    const row = p.rows.find((r) => r['编号'] === code)
    if (!row) throw { response: { data: { message: '表单数据不存在：' + code } } }
    const { detail, ...rest } = row
    return { data: { ...rest }, meta: buildMeta(p.config), privilege: privilege(p.config), detail: p.config.detail, detailData: detail || [], buttonGroups: p.config.metadata.buttonGroups, selectConfig: p.config.selectConfig }
  }
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const p = unwrap(await platformCall((sd) => sd.api.getFormDescriptor({ panelCode: resolvePanelCode(panelCode), code })))
    const cfg = await platformConfig(panelCode)
    const fp = cfg.metadata.panelPageDto.formPages?.[0] || {}
    return {
      data: adaptFormData(p.data),
      meta: adaptMeta(p.meta, p.dataSchema, fp.fieldNames),
      privilege: p.privilege,
      detail: { tabs: [] },
      buttonGroups: panelxButtonGroups(fp.bottomOperationBarBtn),
    }
  }
  return unwrap(await request.get('/px/getFormDescriptor', { params: { panelCode, code } }))
}

export async function queryFormDataList(params) {
  if (USE_MOCK) {
    await mockDelay()
    let rows = flattenFor(params.panelCode)
    const cond = params.condition || {}
    for (const [k, v] of Object.entries(cond)) {
      if (v === undefined || v === null || v === '') continue
      const sv = String(v).toLowerCase()
      rows = rows.filter((r) => String(r[k] ?? '').toLowerCase().includes(sv))
    }
    if (params.keyword) {
      const k = params.keyword.toLowerCase()
      rows = rows.filter((r) => Object.values(r).some((v) => String(v ?? '').toLowerCase().includes(k)))
    }
    const pageNo = params.pageNo || 1
    const pageSize = params.pageSize || 20
    return { totalSize: rows.length, list: rows.slice((pageNo - 1) * pageSize, pageNo * pageSize) }
  }
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const p = unwrap(await platformCall((sd) => sd.api.queryFormDataList({ ...params, panelCode: resolvePanelCode(params.panelCode) })))
    return { totalSize: p.totalSize ?? 0, list: p.list || [] }
  }
  return unwrap(await request.post('/px/queryFormDataList', params))
}

// 演示环境未实现的操作（界面照 T+ 展示，点击给提示）
const UNIMPLEMENTED = new Set([
  '引入常用单据', '设置默认功能', '选销售订单', '修改', '审批情况', '保存为常用单据', '保存打印',
  '生成材料出库单', '生成材料出库单(分单)', '生成材料出库单(退料)',
  '生成工序汇报单（自制汇报）', '生成工序汇报单（委外汇报）',
  '生成工序汇报单（自制汇报退回）', '生成工序汇报单（委外汇报退回）',
  '生成产成品入库单', '生成产成品入库单(退库)',
  '生成补投生产加工单（按累计报废入库数量）', '生成返工生产加工单（按累计报废入库数量）',
  '变更', '现存量查询', '变更历史', '联查', '执行情况', '销售订单情况', '材料出库情况',
  // 工序汇报单 MR10 未实现项（界面照 T+ 展示，点击提示）
  '确认变更', '退出', '工序条码录入', '生成产成品入库单（废品）', '审批情况',
  '工序汇报情况', '产成品入库情况', '加工单执行图', '返工/补投加工情况', '生单流程联查',
  '单据设置', '移动控件位置', '调整控件宽度', '工具栏设置',
  '直接打印', '打印', '预览', '打印模板设置', '导出', '明细标签打印', '工序流转卡打印', '打印情况',
  '复制', '重取BOM材料（仅追加）', '附件', '消息', '选单',
  // 基础档案/查询面板未实现项（界面照 T+ 展示）
  '查找', '打印', '预览', '下载模板', '导入', '导出', '退出', '发送邮件', '更新单据', '设置', '批量取成本', '汇总维度设置', '汇总导出', '汇总导入', '模板下载', '下载工艺路线模板', '导入工艺路线', 'BOM展开', '下载分类模板', '导入分类', '下载价格本列导入模板', '价格本列导入', '批量修改', '标签模板', '标签打印', '价签打印', '现存量提取', '现存量参照', '栏目',
  // SO_ORDER 销售订单未实现项
  '生成销售出库单(普通销售)', '生成销售出库单(销售退货)', '生成销售订单(销售退货)', '转成产成品入库单', '协同-生成对方销售订单',
  '出库情况', '生产加工情况', '订单执行图', '退货情况', '联查合同', '导入', '下载导入模板', '单据分享', '变更', '变更历史',
  // 库存核算 6 单据未实现项（界面照 T+ 展示）
  '引入常用单据', '设置默认功能', '智能选单', '保存为常用单据', '保存打印', '协同',
  '转成材料出库单', '转成销售出库单', '转换成其他出库单', '转换成其他入库单',
  '生成产成品入库单（自制退库）', '生成补投生产加工单', '生成返工生产加工单',
  '生成材料出库单（直接退料）', '生成材料出库单（自制退料）',
  '选产成品入库单（自制加工）', '选生产加工单', '选生产加工单(新增材料)',
  '选材料出库单（直接领料）', '选材料出库单（自制领料）', '选单转换',
  '其他出库单', '重新取价', '入库调整情况', '其他出库情况', '其他入库情况', '退库情况', '退料情况', '联查设备投放单',
])

// 推式生单：销售订单 → 生成生产加工单（对齐真实 T+「生单-生成生产加工单」）
// 字段对应：单据日期→合同号、单据编号→销售订单号、客户/客户编码→客户/客户编码、结算客户→生产订单客户、
//           业务员→负责人、预计交货日期→预完工日；明细行→产成品明细（存货编码→产品编码等）
function createMoFromSo(soForm) {
  const src = (soForm?.['编号'] && SO_ROWS.find((r) => r['编号'] === soForm['编号'])) || soForm || {}
  const items = src?.detail?.items || []
  const newNo = nextIngotNo()
  const now = new Date().toLocaleString('zh-CN', { hour12: false })
  const mo = {
    编号: newNo, 单据状态: '草稿', 创建时间: now, 更新时间: now, 发起人编号: 'tplusdemo12855',
    合同号: src['单据日期'] || today, 锭号: newNo, 批号: '正常', 生产车间: '熔铸车间',
    预开工日: today, 预完工日: src['预计交货日期'] || '',
    销售订单号: src['单据编号'] || '', 客户编码: src['客户编码'] || '', 客户: src['客户'] || '',
    测试程序: '光谱分析', 生产订单客户: src['结算客户'] || '',
    机构: '总部', 重量: 0, 开工日期: '', 完工日期: '', 启用派工: false, 自动转移: false,
    产品自动添加到材料: false, 是否手工修改单据编码: false, 外部单据号: '', 负责人: src['业务员'] || '',
    启用领料申请: false, 对方仓库: '',
    detail: {
      products: items.map((it) => P({
        产品编码: it['存货编码'] || '', 产品名称: it['存货名称'] || '', 规格型号: it['规格型号'] || '',
        生产单位: it['销售单位'] || '件', 数量: it['数量'] || 0, 现存量: it['现存量'] || 0,
        需求令号: src['单据编号'] || '',
      })),
      materials: [],
      processes: [],
    },
  }
  MOCK_ROWS.unshift(mo)
  return { 编号: newNo, 单据状态: '草稿', gotoPanel: 'MANU_ORDER' }
}

export async function callButton({ panelCode, buttonName, formData, buttonParam }) {
  if (USE_MOCK) {
    await mockDelay()
    if (UNIMPLEMENTED.has(buttonName)) {
      throw { response: { data: { message: `演示环境暂未实现「${buttonName}」，界面与 T+ 保持一致` } } }
    }
    // 推式生单：销售订单 → 生成生产加工单
    if (panelCode === 'SO_ORDER' && buttonName === '生成生产加工单') {
      return createMoFromSo(formData)
    }
    const p = panelOf(panelCode)
    const rows = p.rows
    const no = formData?.['编号']
    if (buttonName === '刷新') return {}
    if (buttonName === '新增' || buttonName === '新增流程' || buttonName === '保存' || buttonName === '保存为草稿' || buttonName === '保存新增') {
      const { detail, ...rest } = formData || {}
      delete rest['编号']
      delete rest['创建时间']
      delete rest['更新时间']
      delete rest['发起人编号']
      if (no) {
        const row = rows.find((r) => r['编号'] === no)
        if (!row) throw { response: { data: { message: '表单数据不存在：' + no } } }
        if (row['单据状态'] !== '草稿') throw { response: { data: { message: '仅草稿状态可保存' } } }
        Object.assign(row, rest, { 单据状态: '草稿', detail: detail || {}, 更新时间: new Date().toLocaleString('zh-CN', { hour12: false }) })
        return { 编号: no, 单据状态: '草稿' }
      }
      const newNo = nextNoFor(panelCode)
      const now = new Date().toLocaleString('zh-CN', { hour12: false })
      const _af2 = p.config.metadata.autoCodeField
      rows.unshift({ ...rest, 编号: newNo, ...(_af2 ? { [_af2]: newNo } : {}), 单据状态: '草稿', detail: detail || {}, 创建时间: now, 更新时间: now, 发起人编号: 'tplusdemo12855' })
      return { 编号: newNo, 单据状态: '草稿' }
    }
    if (buttonName === '删除') {
      const idx = rows.findIndex((r) => r['编号'] === no)
      if (idx < 0) throw { response: { data: { message: '表单数据不存在：' + no } } }
      if (rows[idx]['单据状态'] !== '草稿') throw { response: { data: { message: '仅草稿状态可删除' } } }
      rows.splice(idx, 1)
      return {}
    }
    // 审批流（mock 模拟 T+ 工作流审批：提交审批 → 审批中 → 审批通过(已审核+已审批) / 驳回(草稿)）
    if (['提交审批', '审批通过', '驳回审批'].includes(buttonName)) {
      const row = rows.find((r) => r['编号'] === no)
      if (!row) throw { response: { data: { message: '表单数据不存在：' + no } } }
      if (buttonName === '提交审批' && row['单据状态'] !== '草稿') throw { response: { data: { message: '仅草稿状态可提交审批' } } }
      if ((buttonName === '审批通过' || buttonName === '驳回审批') && row['单据状态'] !== '审批中') throw { response: { data: { message: '仅审批中状态可审批' } } }
      const now = new Date().toLocaleString('zh-CN', { hour12: false })
      if (buttonName === '提交审批') {
        row['单据状态'] = '审批中'
        row['审批状态'] = '待审批'
        row['审批提交时间'] = now
      } else if (buttonName === '审批通过') {
        row['单据状态'] = '已审核'
        row['审批状态'] = '已审批'
        row['审批人'] = '系统管理员'
        row['审批时间'] = now
      } else {
        row['单据状态'] = '草稿'
        row['审批状态'] = '已驳回'
        row['审批人'] = '系统管理员'
        row['审批时间'] = now
      }
      row['更新时间'] = now
      return { 编号: no, 单据状态: row['单据状态'], 审批状态: row['审批状态'] }
    }
    if (['审核', '弃审', '中止执行', '整单中止', '草稿', '取消中止'].includes(buttonName)) {
      const row = rows.find((r) => r['编号'] === no)
      if (!row) throw { response: { data: { message: '表单数据不存在：' + no } } }
      if (buttonName === '审核' && row['单据状态'] !== '草稿') throw { response: { data: { message: '仅草稿状态可审核' } } }
      if (buttonName === '弃审' && row['单据状态'] !== '已审核') throw { response: { data: { message: '仅已审核状态可弃审' } } }
      if (['中止执行', '整单中止'].includes(buttonName) && !['已审核', '生产中', '已完工'].includes(row['单据状态'])) throw { response: { data: { message: '仅已审核/生产中/已完工状态可中止' } } }
      if (buttonName === '草稿' && row['单据状态'] !== '已中止') throw { response: { data: { message: '仅已中止状态可恢复草稿（取消中止）' } } }
      row['单据状态'] = buttonName === '审核' ? '已审核' : buttonName === '弃审' ? '草稿' : ['中止执行', '整单中止'].includes(buttonName) ? '已中止' : '已审核'
      if (buttonName === '弃审') row['审批状态'] = ''
      row['更新时间'] = new Date().toLocaleString('zh-CN', { hour12: false })
      return { 编号: no, 单据状态: row['单据状态'] }
    }
    if (buttonName === '放弃') return {}
    throw { response: { data: { message: '未定义按钮规则：' + buttonName } } }
  }
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const res = await platformCall((sd) => sd.api.callButton({ panelCode: resolvePanelCode(panelCode), buttonName, formData, buttonParam }))
    return unwrap(res)
  }
  // 真实接口：按钮名对齐后端（中止执行/整单中止→中止、草稿→取消中止）
  const apiName = buttonName === '中止执行' || buttonName === '整单中止' ? '中止' : buttonName === '草稿' ? '取消中止' : buttonName === '保存' || buttonName === '保存为草稿' || buttonName === '保存新增' ? '提交' : buttonName
  return unwrap(await request.post('/px/callButton', { panelCode, buttonName: apiName, formData, buttonParam }))
}

export async function deleteForms({ panelCode, rowCodes }) {
  if (USE_MOCK) {
    await mockDelay()
    const rows = panelOf(panelCode).rows
    for (const code of rowCodes) {
      const idx = rows.findIndex((r) => r['编号'] === code)
      if (idx >= 0) {
        if (rows[idx]['单据状态'] !== '草稿') throw { response: { data: { message: '仅草稿状态可删除' } } }
        rows.splice(idx, 1)
      }
    }
    return true
  }
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const res = await platformCall((sd) => sd.api.deleteForms({ panelCode: resolvePanelCode(panelCode), rowCodes }))
    return unwrap(res)
  }
  return unwrap(await request.post('/px/deleteForms', { panelCode, rowCodes }))
}

// 工具栏快捷键提示（对齐真实 T+ 按钮）
export const SHORTCUTS = {
  保存: 'Alt+S',
  保存新增: 'Alt+\\',
  保存打印: 'Alt+G',
  直接打印: 'Alt+P',
  打印: 'Alt+;',
  预览: 'Alt+/',
  打印模板设置: 'Alt+,',
  导出: 'Alt+X',
  放弃: 'Alt+Z',
}

