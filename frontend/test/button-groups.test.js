import assert from 'node:assert/strict'
import test from 'node:test'

import {
  approvalSecondaryOf,
  ensureAuditAction,
  ensureColumnPrefsAction,
  ensureFieldManageAction,
  ensureScanFillAction,
  ensureSelectAction,
  hasSelectConfig,
  isApprovalGroup,
  primaryActionOf,
  supportsAudit,
} from '../src/core/button-groups.js'

test('adds scan fill to an editable sales document with an old toolbar config', () => {
  const source = [{
    name: '更多',
    actions: ['放弃', '草稿', '导入', '下载导入模板', '整单中止', '附件', '刷新', '消息'],
  }]

  const result = ensureScanFillAction(source, { panelCategory: '单据' })

  assert.deepEqual(result[0].actions, [
    '放弃', '草稿', '导入', '下载导入模板', '整单中止', '附件', '刷新', '消息', '扫描填单',
  ])
  assert.deepEqual(source[0].actions, [
    '放弃', '草稿', '导入', '下载导入模板', '整单中止', '附件', '刷新', '消息',
  ])
})

test('deduplicates scan fill and creates the more group when needed', () => {
  const existing = [{ name: '更多', actions: ['刷新', '扫描填单', '扫描填单'] }]
  const deduplicated = ensureScanFillAction(existing, { panelCategory: '单据' })
  const created = ensureScanFillAction([], { panelCategory: '单据' })

  assert.deepEqual(deduplicated[0].actions, ['刷新', '扫描填单'])
  assert.deepEqual(created, [{ name: '更多', actions: ['刷新', '扫描填单'] }])
})

test('supports editable opening-balance documents', () => {
  const result = ensureScanFillAction([], { panelCategory: '期初单据' })

  assert.deepEqual(result, [{ name: '更多', actions: ['刷新', '扫描填单'] }])
})

test('does not expose scan fill on readonly or non-document panels', () => {
  const groups = [{ name: '更多', actions: ['刷新', '扫描填单'] }]

  assert.deepEqual(
    ensureScanFillAction(groups, { panelCategory: '单据', readonly: true }),
    [{ name: '更多', actions: ['刷新'] }],
  )
  assert.deepEqual(
    ensureScanFillAction(groups, { panelCategory: '报表' }),
    [{ name: '更多', actions: ['刷新'] }],
  )
})

test('keeps a descriptor-provided scan action when metadata is unavailable', () => {
  const groups = [{ name: '更多', actions: ['刷新', '扫描填单', '扫描填单'] }]

  assert.deepEqual(
    ensureScanFillAction(groups, null),
    [{ name: '更多', actions: ['刷新', '扫描填单'] }],
  )
})

/* ── 字段管理入口（面板自助加字段）── */
test('字段管理入口挂在「更多」组，且不改动原数组', () => {
  const source = [{ name: '更多', actions: ['刷新'] }]
  const result = ensureFieldManageAction(source, true)

  assert.deepEqual(result[0].actions, ['刷新', '表格调整', '字段管理'])
  assert.deepEqual(source[0].actions, ['刷新'])
})

test('字段管理入口不重复、没有「更多」组时新建', () => {
  assert.deepEqual(
    ensureFieldManageAction([{ name: '更多', actions: ['表格调整', '字段管理'] }], true),
    [{ name: '更多', actions: ['表格调整', '字段管理'] }],
  )
  assert.deepEqual(
    ensureFieldManageAction([], true),
    [{ name: '更多', actions: ['刷新', '表格调整', '字段管理'] }],
  )
})

test('无权限或引擎不支持时确保入口不存在', () => {
  const groups = [{ name: '更多', actions: ['刷新', '字段管理'] }]
  assert.deepEqual(ensureFieldManageAction(groups, false), [{ name: '更多', actions: ['刷新'] }])
})

test('表格调整与字段管理可以叠加（顺序稳定）', () => {
  const chained = ensureFieldManageAction(
    ensureColumnPrefsAction([{ name: '更多', actions: ['刷新'] }], true),
    true,
  )
  assert.deepEqual(chained[0].actions, ['刷新', '表格调整', '字段管理'])
})

/* ── 审核入口（列表页此前只有「审批」，草稿无法审核）── */
const quoteMetadata = {
  panelCategory: '单据',
  panelPageDto: {
    formPages: [{ bottomOperationBarBtn: [{ buttonName: '保存' }, { buttonName: '审核' }] }],
  },
}

test('支持审核的面板把「审核」并入审批组并置于首位', () => {
  const source = [{ name: '审批', actions: ['审批通过', '审批驳回', '审批情况', '弃审'] }]

  const result = ensureAuditAction(source, true)

  assert.deepEqual(result[0].actions, ['审核', '审批通过', '审批驳回', '审批情况', '弃审'])
  assert.deepEqual(source[0].actions, ['审批通过', '审批驳回', '审批情况', '弃审'], '不改动原数组')
})

test('没有审批组时新建一个只含「审核」的组', () => {
  assert.deepEqual(ensureAuditAction([{ name: '更多', actions: ['刷新'] }], true), [
    { name: '更多', actions: ['刷新'] },
    { name: '审批', actions: ['审核'] },
  ])
})

test('不支持审核的面板确保「审核」不出现，且原有组保持不变', () => {
  const groups = [{ name: '审批', actions: ['审核', '审批通过'] }]

  assert.deepEqual(ensureAuditAction(groups, false), [{ name: '审批', actions: ['审批通过'] }])
})

test('面板自带「审核」组时不再往审批组复制一份（避免出现两个审核入口）', () => {
  const groups = [{ name: '审核', actions: ['审核', '弃审', '审批情况'] }, { name: '更多', actions: ['刷新'] }]

  assert.deepEqual(ensureAuditAction(groups, true), [
    { name: '审核', actions: ['审核', '弃审', '审批情况'] },
    { name: '更多', actions: ['刷新'] },
  ])
})

test('supportsAudit 读取表单页底部按钮，报表/只读/无审核时不支持', () => {
  assert.equal(supportsAudit(quoteMetadata), true)
  assert.equal(supportsAudit({ ...quoteMetadata, panelCategory: '报表' }), false)
  assert.equal(supportsAudit({ ...quoteMetadata, readonly: true }), false)
  assert.equal(supportsAudit({ panelCategory: '单据', panelPageDto: { formPages: [] } }), false)
  assert.equal(supportsAudit({ panelButtons: ['保存', '审核'], panelCategory: '单据' }), true)
  assert.equal(supportsAudit(null), false)
})

test('hasSelectConfig 认 selectConfig / selectConfigs，两者都没有就是没有来源', () => {
  assert.equal(hasSelectConfig({ selectConfig: { source: 'SO_ORDER' } }), true)
  assert.equal(hasSelectConfig({ selectConfigs: { 选销售订单: { source: 'SO_ORDER' } } }), true)
  assert.equal(hasSelectConfig({ selectConfigs: {} }), false)
  assert.equal(hasSelectConfig({ selectConfig: null }), false)
  assert.equal(hasSelectConfig({ panelCode: 'SO_ORDER' }), false)
  assert.equal(hasSelectConfig(null), false)
})

test('无选单来源时整组隐藏「选单」，并从其他组摘掉「选单」动作', () => {
  const source = [
    { name: '新增', actions: ['新增', '引入常用单据'] },
    { name: '选单', actions: ['选销售订单', '设置默认功能'] },
    { name: '保存', actions: ['保存', '选单'] },
  ]

  const result = ensureSelectAction(source, false)

  assert.deepEqual(result, [
    { name: '新增', actions: ['新增', '引入常用单据'] },
    { name: '保存', actions: ['保存'] },
  ])
  assert.deepEqual(source[1].actions, ['选销售订单', '设置默认功能'], '不改动原数组')
})

test('有选单来源时保持原样；过滤后变空的组不再保留', () => {
  const groups = [
    { name: '选单', actions: ['选单'] },
    { name: '更多', actions: ['刷新'] },
  ]

  assert.deepEqual(ensureSelectAction(groups, true), groups)
  assert.deepEqual(
    ensureSelectAction([{ name: '选单', actions: ['选单'] }, { name: '更多', actions: ['刷新'] }], false),
    [{ name: '更多', actions: ['刷新'] }],
  )
})

// ---------- 审批主按钮随状态走（方案 A） ----------
const approvalGroup = {
  name: '审批',
  actions: ['提交审批', '审批通过', '审批驳回', '审批情况', '弃审'],
}

/** 与 PanelxList.isDisabled 同口径的审批部分 */
function disabledByStatus(status) {
  return (action) => ({
    提交审批: status !== '草稿',
    审批通过: status !== '审批中',
    审批驳回: status !== '审批中',
    审批情况: false,
    弃审: status !== '已审核',
  })[action] === true
}

test('isApprovalGroup 认审批流动作，普通组不误判', () => {
  assert.equal(isApprovalGroup(approvalGroup), true)
  assert.equal(isApprovalGroup({ name: '审批', actions: ['审核', '审批通过'] }), true)
  assert.equal(isApprovalGroup({ name: '保存', actions: ['保存', '保存新增'] }), false)
  assert.equal(isApprovalGroup({ name: '更多', actions: ['刷新'] }), false)
})

test('审批主按钮跟着单据状态走：草稿提交审批、审批中审批通过、已审核弃审', () => {
  assert.equal(primaryActionOf(approvalGroup, disabledByStatus('草稿')), '提交审批')
  assert.equal(primaryActionOf(approvalGroup, disabledByStatus('审批中')), '审批通过')
  assert.equal(primaryActionOf(approvalGroup, disabledByStatus('已审核')), '弃审')
  // 审批情况是只读查看，永远排在状态流转之后
  assert.equal(primaryActionOf(approvalGroup, disabledByStatus('已中止')), '审批情况')
})

test('非审批组主按钮仍是第一个动作（禁用就是禁用，不替换成别的动作）', () => {
  const save = { name: '保存', actions: ['保存', '保存新增'] }
  assert.equal(primaryActionOf(save, (a) => a === '保存'), '保存')
  assert.equal(primaryActionOf({ name: '保存', actions: [] }, () => false), '')
})

test('审批中把「审批驳回」并列露出，其它状态不露出', () => {
  const primary = primaryActionOf(approvalGroup, disabledByStatus('审批中'))
  assert.deepEqual(approvalSecondaryOf(approvalGroup, primary, disabledByStatus('审批中')), ['审批驳回'])

  const draftPrimary = primaryActionOf(approvalGroup, disabledByStatus('草稿'))
  assert.deepEqual(approvalSecondaryOf(approvalGroup, draftPrimary, disabledByStatus('草稿')), [])
  assert.deepEqual(approvalSecondaryOf({ name: '保存', actions: ['保存'] }, '保存', () => false), [])
})
