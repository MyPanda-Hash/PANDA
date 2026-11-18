import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  APPROVAL_PANELS,
  isApprovalStall,
  isUnapprovedStatus,
  requiresApproval,
} from '../src/business/approval-panels.js'

/**
 * 审批标识：业务总览的节点靠这份清单判断要不要显示「审批」角标，
 * 清单必须与后端 PxService#APPROVAL_PANELS 完全一致，否则图上标的和后端实际能审批的对不上。
 */
test('审批面板清单与后端 PxService 保持一致', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(
    path.join(here, '../../backend/src/main/java/com/mes/service/PxService.java'), 'utf8')
  const start = source.indexOf('private static final Set<String> APPROVAL_PANELS = Set.of(')
  assert.ok(start > 0, '找不到后端 APPROVAL_PANELS')
  const block = source.slice(start, source.indexOf(');', start))
  const backend = new Set([...block.matchAll(/"([A-Z_]+)"/g)].map((m) => m[1]))

  assert.deepEqual([...APPROVAL_PANELS].sort(), [...backend].sort(),
    '前端审批清单漂移了：改了后端 APPROVAL_PANELS 要同步改 approval-panels.js')
})

test('只有审批面板返回 true，其余（含空值）为 false', () => {
  assert.equal(requiresApproval('SO_ORDER'), true)
  assert.equal(requiresApproval('SALE_COST_ALLOC'), true)
  assert.equal(requiresApproval('DISPATCH'), false, '排产批次不走审批')
  assert.equal(requiresApproval('PROCESS_REPORT'), false, '报工免审批')
  assert.equal(requiresApproval(''), false)
  assert.equal(requiresApproval(null), false)
})

test('未审批 = 草稿 / 审批中，其余状态都不算', () => {
  assert.equal(isUnapprovedStatus('草稿'), true)
  assert.equal(isUnapprovedStatus('审批中'), true)
  assert.equal(isUnapprovedStatus('已审核'), false)
  assert.equal(isUnapprovedStatus('生产中'), false)
  assert.equal(isUnapprovedStatus('作废'), false)
})

test('卡在未审批：卡点 + 草稿/审批中；已审核的卡点不算', () => {
  assert.equal(isApprovalStall({ 是否卡点: true, 单据状态: '草稿' }), true)
  assert.equal(isApprovalStall({ 是否卡点: true, 单据状态: '审批中' }), true)
  assert.equal(isApprovalStall({ 是否卡点: true, 单据状态: '已审核' }), false,
    '已审核却卡住是下游没跟上，不能标成待审批')
  assert.equal(isApprovalStall({ 是否卡点: false, 单据状态: '草稿' }), false,
    '没被判为卡点的单据只显示普通审批角标，不流光')
  assert.equal(isApprovalStall(null), false)
})

/**
 * 业务总览整页滚动的回归哨：`.bo-page` 只能写 min-height。
 * 写 height:100% 时内容一超过视口，外层滚动容器的高度就等于视口，
 * 于是滚动条不出现、页面下半截被裁掉（追踪 39 环节时必然触发）。
 */
test('业务总览页面用 min-height 撑开滚动，不再写死 height:100%', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(
    path.join(here, '../src/views/scm/BusinessOverview.vue'), 'utf8')
  const start = source.indexOf('.bo-page {')
  assert.ok(start > 0, '找不到 .bo-page 样式')
  // 先去掉注释再断言：注释里写着「不能写 height:100%」不算违规
  const block = source.slice(start, source.indexOf('}', start)).replace(/\/\*[\s\S]*?\*\//g, '')
  assert.ok(block.includes('min-height: 100%'), '.bo-page 必须用 min-height 撑开')
  assert.ok(!/(^|[\s;{])height:\s*100%/.test(block), '.bo-page 不能再写 height:100%（会裁掉下半截且滚不动）')
  const bodyStart = source.indexOf('.bo-body {')
  const body = source.slice(bodyStart, source.indexOf('}', bodyStart))
  assert.ok(/flex:\s*1\s+0\s+auto/.test(body), '.bo-body 不能被压扁，要能随内容撑开')
  assert.ok(/min-height:\s*\d+px/.test(body), '.bo-body 要给一个最小高度，追踪路线长时图表不至于消失')
})
