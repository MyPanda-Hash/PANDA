/**
 * 修正历史报检单与加工单对不上的数据（2026-11-02）
 *
 * 问题：BJ-2026-08-0001（成品报检单，来源 MO-2026-08-0009）
 *   报检总量 350（CP001 200 + CP002 100 + CP004 50）
 *   但加工单 MO-2026-08-0009 的真实情况是：
 *     CP001 计划 200、累计汇报 160
 *     CP002 计划 100、累计汇报 0
 *     CP004 根本不在该加工单的产品明细里
 *   于是出现"还在生产中（累计完工 160）却已报检 350"的矛盾。
 *
 * 修正口径（不回算、只让单据自洽）：
 *   1. 删除 CP004 行（不在加工单的产品明细里）
 *   2. 各行报检数量收敛到不超累计汇报量；累计汇报为 0 的 CP002 整行删除
 *   3. 保持"合格 + 不合格 = 检验数量"守恒，重新分配 2 件不合格
 *
 * 用法：node tools/fix-inspect-quantity.cjs [--dry]
 */
const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api'
const DRY = process.argv.includes('--dry')

const TARGET = { panel: 'FINISH_INSPECT', no: 'BJ-2026-08-0001', moNo: 'MO-2026-08-0009' }

let token = null
const results = []

function log(ok, name, extra = '') {
  results.push({ ok, name, extra })
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`)
}

async function api(path, body) {
  const res = await fetch(BASE + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return res.json()
}

async function button(panelCode, buttonName, formData = {}) {
  return api('/px/callButton', { panelCode, buttonName, formData, buttonParam: {} })
}

async function listOf(panelCode, pageSize = 200) {
  const r = await api('/px/queryFormDataList', { panelCode, pageNo: 1, pageSize })
  return r.code === 200 ? (r.data?.list || []) : []
}

function noOf(row) {
  return row['编号'] || row['单据编号'] || ''
}

async function driveToAudited(panel, no) {
  const a = await button(panel, '审核', { 编号: no })
  if (a.code !== 200) throw new Error('审核: ' + (a.message || ''))
  const st = String(a.data?.['单据状态'] || '')
  if (st === '审批中') {
    const p = await button(panel, '审批通过', { 编号: no })
    if (p.code !== 200) throw new Error('审批通过: ' + (p.message || ''))
    return '审核→审批中→审批通过'
  }
  return '审核→' + (st || '已审核')
}

async function main() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' })
  if (login.code !== 200) {
    console.log('登录失败:', JSON.stringify(login).slice(0, 200))
    process.exit(1)
  }
  token = login.data.token
  console.log(`===== 修正报检单数量${DRY ? '（预演）' : ''} =====\n`)

  // 1. 读加工单的真实完工量
  const moList = await listOf('MANU_ORDER', 100)
  const mo = moList.find((r) => noOf(r) === TARGET.moNo)
  if (!mo) throw new Error('找不到加工单 ' + TARGET.moNo)
  const finished = {}
  for (const p of mo.detail?.products || []) {
    const code = p['产品编码']
    if (!code) continue
    finished[code] = {
      planned: Number(p['数量']) || 0,
      reported: Number(p['累计汇报套数(工序单位)']) || 0,
      name: p['产品名称'] || '',
    }
  }
  console.log(`加工单 ${TARGET.moNo} [${mo['单据状态']}] 产品完工情况：`)
  for (const [code, v] of Object.entries(finished)) {
    console.log(`  ${code} ${v.name} 计划=${v.planned} 累计完工=${v.reported}`)
  }

  // 2. 读报检单当前明细
  const inspectList = await listOf(TARGET.panel, 200)
  const doc = inspectList.find((r) => noOf(r) === TARGET.no)
  if (!doc) throw new Error('找不到报检单 ' + TARGET.no)

  const oldRows = doc.detail?.items || []
  console.log(`\n报检单 ${TARGET.no} [${doc['单据状态']}] 修正前：`)
  let oldTotal = 0
  for (const r of oldRows) {
    oldTotal += Number(r['报检数量']) || 0
    console.log(`  ${r['存货编码']} ${r['产品名称']} 报检=${r['报检数量']} 合格=${r['合格数量']} 不合格=${r['不合格数量']}`)
  }
  console.log(`  合计报检 ${oldTotal}`)

  // 3. 重算：只保留加工单里有且已完工的产品，数量不超累计完工
  const newRows = []
  for (const r of oldRows) {
    const code = r['存货编码']
    const info = finished[code]
    if (!info) {
      console.log(`  → 删除 ${code} ${r['产品名称']}：不在加工单 ${TARGET.moNo} 的产品明细里`)
      continue
    }
    const want = Number(r['报检数量']) || 0
    if (info.reported <= 0) {
      console.log(`  → 删除 ${code} ${r['产品名称']}：加工单累计完工为 0，尚不可报检`)
      continue
    }
    const qty = Math.min(want, info.reported)
    // 保持合格 + 不合格 = 检验数量：按原不合格率重算，至少 0
    const oldUnq = Number(r['不合格数量']) || 0
    const rate = want > 0 ? oldUnq / want : 0
    const unq = Math.min(Math.round(qty * rate), qty)
    const q = qty - unq
    newRows.push({
      ...r,
      报检数量: qty,
      合格数量: q,
      不合格数量: unq,
      检验结果: unq > 0 ? '合格' : '合格',
    })
    console.log(`  → 保留 ${code} ${r['产品名称']}：报检 ${want} 收敛为 ${qty}（合格 ${q} 不合格 ${unq}）`)
  }

  autoRecomputeTotals()
  const newTotal = newRows.reduce((s, r) => s + (Number(r['报检数量']) || 0), 0)
  console.log(`\n修正后合计报检 ${newTotal}（应 ≤ 累计完工 ${Object.values(finished).reduce((s, v) => s + v.reported, 0)}）`)

  if (!newRows.length) throw new Error('修正后没有可用明细行，请人工检查')
  if (DRY) {
    console.log('\n（预演，不写库）')
    return
  }

  // 4. 审核中的单要先弃审才能改
  let status = doc['单据状态'] || ''
  if (status === '已审核') {
    const un = await button(TARGET.panel, '弃审', { 编号: TARGET.no })
    if (un.code !== 200) throw new Error('弃审: ' + (un.message || ''))
    status = '草稿'
    console.log('已弃审')
  }

  // 5. 保存修正后的明细（带上原表头，避免被清空）
  const head = {}
  for (const [k, v] of Object.entries(doc)) {
    if (['detail', 'meta', '单据状态', '创建时间', '更新时间', '审核人', '审核时间', '审核意见',
         '审批状态', '提交人', '提交时间'].includes(k)) continue
    head[k] = v
  }
  head['编号'] = TARGET.no
  head.detail = { items: newRows }

  const saved = await button(TARGET.panel, '保存', head)
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''))
  log(true, '保存修正明细', `${newRows.length} 行，合计 ${newTotal}`)

  // 6. 重新审核，验证新校验不拦自己（数据已自洽）
  const note = await driveToAudited(TARGET.panel, TARGET.no)
  log(true, '重新审核', note)

  console.log('\n===== 完成 =====')
}

/** 依次重算 0 元辅助，保持脚本结构清晰（实际计算在 newRows 内完成）。 */
function autoRecomputeTotals() {}

main().catch((e) => {
  console.error('异常:', e.message)
  process.exit(1)
})
