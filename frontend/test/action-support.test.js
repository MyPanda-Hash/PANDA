import assert from 'node:assert/strict'
import test from 'node:test'

import { filterSupportedActions, isActionSupported, isSelectAction } from '../src/core/action-support.js'
import { isCatchAllGroup } from '../src/core/button-groups.js'

test('实现过的动作判为支持，配置里的占位动作判为不支持', () => {
  assert.equal(isActionSupported('保存'), true)
  // 2026-11-05：后端 PxService.callButton 没有「生成销售退货单」的 case，default 分支会抛
  // 「未定义按钮规则」。此前这里断言 true，与 tools/action-support-audit.mjs 的解析结果矛盾，
  // 按钮在界面上可见、点了必然 409。以 Java 实现为唯一口径，改为不支持。
  assert.equal(isActionSupported('生成销售退货单'), false)
  assert.equal(isActionSupported('审批通过'), true)
  assert.equal(isActionSupported('变更'), true, 'PanelActionHandler 注册的动作')
  assert.equal(isActionSupported('选工艺路线'), true, '手工建单按路线带出工序，后端有 switch 分支')
  assert.equal(isActionSupported('扫描填单'), true, '前端自己实现')
  assert.equal(isActionSupported('附件'), false)
  assert.equal(isActionSupported('消息'), false)
  assert.equal(isActionSupported('移动控件位置'), false)
  assert.equal(isActionSupported('调整控件宽度'), false)
  assert.equal(isActionSupported('工具栏设置'), false)
  assert.equal(isActionSupported('现存量查询'), false)
  assert.equal(isActionSupported('生成批号'), false)
  assert.equal(isActionSupported(''), false)
  assert.equal(isActionSupported(null), false)
})

test('选单类动作只在面板配了来源时才算支持', () => {
  assert.equal(isActionSupported('选单'), false)
  assert.equal(isActionSupported('选单', { hasSelect: true }), true)
  assert.equal(isActionSupported('选销售订单', { hasSelect: true }), true)
  assert.equal(isSelectAction('选销售订单'), true)
  assert.equal(isSelectAction('保存'), false)
})

test('报表专属动作只在报表模式可用，预览在列表页也能用', () => {
  for (const action of ['导出', '打印', '发送邮件', '退出']) {
    assert.equal(isActionSupported(action), false)
    assert.equal(isActionSupported(action, { reportMode: true }), true)
  }
  assert.equal(isActionSupported('预览'), true)
})

test('过滤按钮组：摘掉未实现动作、清空的组去掉、不改原数组', () => {
  const source = [
    { name: '设置', actions: ['移动控件位置', '调整控件宽度', '工具栏设置'] },
    { name: '更多', actions: ['附件', '刷新', '消息', '草稿'] },
    { name: '审批', actions: ['提交审批', '审批通过'] },
  ]

  assert.deepEqual(filterSupportedActions(source), [
    { name: '更多', actions: ['刷新'] },
    { name: '审批', actions: ['提交审批', '审批通过'] },
  ])
  assert.deepEqual(source[0].actions, ['移动控件位置', '调整控件宽度', '工具栏设置'])
  assert.deepEqual(source[1].actions, ['附件', '刷新', '消息', '草稿'])
})

test('isCatchAllGroup 只认收纳组', () => {
  for (const name of ['更多', '工具', '设置', '智能']) assert.equal(isCatchAllGroup({ name }), true)
  for (const name of ['新增', '保存', '审批', '选单', '打印']) assert.equal(isCatchAllGroup({ name }), false)
  assert.equal(isCatchAllGroup(null), false)
})