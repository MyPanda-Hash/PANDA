/**
 * 造卡点样本：工序报检单停在草稿态（2026-11-02）
 *
 * 目的：§14.5 记录的缺口"工序链全部已审核，看不到卡在某道工序的样本"。
 * 本脚本用另一张已完工加工单 MO-2026-08-0008 造一条**故意停在草稿**的链：
 *
 *   PROCESS_REPORT.加工单号      = MO-2026-08-0008  → 汇报单【已审核】
 *   PROCESS_INSPECT_APPLY.来源单号 = 汇报单号        → 报检单【草稿，停在这里】
 *
 * 报检单不审核，追踪时它就会成为"卡点"，验证琥珀色脉冲 + 流光链路。
 *
 * 用法：node tools/seed-stalled-chain.cjs [--dry]
 */
const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api'
const DRY = process.argv.includes('--dry')

/** 用已完工的 MO-2026-08-0008（3 道工序，计划 1350 件）。 */
const SOURCE_MO = 'MO-2026-08-0008'

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

/** 推进到已审核：草稿→审核；若变审批中再审批通过。 */
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

/** 取加工单的工序明细；工序行没有产品字段，用 products[0] 兜底。 */
async function loadSource() {
  const rows = await listOf('MANU_ORDER', 50)
  const mo = rows.find((r) => noOf(r) === SOURCE_MO)
  if (!mo) throw new Error('找不到加工单 ' + SOURCE_MO)
  const procs = mo.detail?.processes || []
  if (!procs.length) throw new Error('加工单没有工序明细')
  const product = (mo.detail?.products || [])[0] || {}
  return { mo, procs, product }
}

// ---------- 1. 工序汇报单（已审核） ----------
async function createReport(mo, procs, product) {
  const created = await button('PROCESS_REPORT', '新增流程', {})
  const autoNo = noOf(created.data || {})
  if (!autoNo) throw new Error('新增未返回编号')

  // 只汇报前一道工序，后续留给报检
  const first = procs[0]
  const plan = Number(first['计划数量']) || 0
  const qualified = Math.round(plan * 0.98)

  const items = [{
    加工单号: SOURCE_MO,
    产品编码: product['产品编码'] || 'CP002',
    产品名称: product['产品名称'] || '铝板 6061',
    规格型号: product['规格型号'] || '1500×3000×2',
    工艺类型: first['工艺类型'] || '自制',
    工艺序号: first['工艺序号'] || 0,
    加工顺序: first['加工顺序'] || 1,
    工序编码: first['工序编码'],
    工序名称: first['工序名称'],
    生产车间: first['生产车间'] || '轧制车间',
    工序单位: first['工序单位'] || '件',
    报工数量: plan,
    合格数量: qualified,
    不合格数量: plan - qualified,
    工价: first['工价'] || 0,
    '计时/计件金额': Number((plan * (first['工价'] || 0)).toFixed(2)),
    金额: Number((plan * (first['工价'] || 0)).toFixed(2)),
    设备: first['设备'] || '',
    班组名称: first['班组'] || '',
    工人名称: first['工人'] || '',
    需求令号: mo['销售订单号'] || '',
  }]

  const saved = await button('PROCESS_REPORT', '保存', {
    编号: autoNo,
    单据日期: '2026-11-02',
    业务类型: '工序汇报',
    加工单号: SOURCE_MO,
    匹配来源单号: SOURCE_MO,
    销售订单号: mo['销售订单号'] || '',
    客户: mo['客户'] || '',
    生产车间: items[0].生产车间,
    产品编码: items[0].产品编码,
    产品名称: items[0].产品名称,
    规格型号: items[0].规格型号,
    测试程序: '硬度测试',
    detail: { items },
  })
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''))

  return { no: autoNo, note: await driveToAudited('PROCESS_REPORT', autoNo), items }
}

// ---------- 2. 工序报检单（故意停在草稿） ----------
async function createStalledApply(report) {
  const created = await button('PROCESS_INSPECT_APPLY', '新增流程', {})
  const autoNo = noOf(created.data || {})
  if (!autoNo) throw new Error('新增未返回编号')

  const items = report.items.map((it, i) => ({
    存货编码: it.产品编码,
    存货名称: it.产品名称,
    规格型号: it.规格型号,
    工序编码: it.工序编码,
    工序名称: it.工序名称,
    计量单位: it.工序单位,
    报检数量: it.合格数量,
    累计检验数量: 0,
    备注: '待检',
    来源面板: 'PROCESS_REPORT',
    来源单号: report.no,
    来源行号: String(i + 1),
    来源数量: it.合格数量,
    累计执行数量: 0,
    累计检验数量: 0,
    累计入库数量: 0,
  }))

  const saved = await button('PROCESS_INSPECT_APPLY', '保存', {
    编号: autoNo,
    单据日期: '2026-11-02',
    业务类型: '工序报检',
    生产车间: report.items[0].生产车间,
    加工单号: SOURCE_MO,
    检验员: '孙鹏',
    来源单号: report.no,
    备注: '故意停在草稿，用于验证卡点',
    detail: { items },
  })
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''))

  // 关键：不审核，保持草稿态
  const check = await listOf('PROCESS_INSPECT_APPLY', 200)
  const row = check.find((r) => noOf(r) === autoNo)
  const status = row?.['单据状态'] || '(未知)'
  return { no: autoNo, status }
}

async function main() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' })
  if (login.code !== 200) {
    console.log('登录失败:', JSON.stringify(login).slice(0, 200))
    process.exit(1)
  }
  token = login.data.token
  console.log(`===== 造卡点样本${DRY ? '（预演）' : ''} =====\n`)

  const { mo, procs, product } = await loadSource()
  console.log(`源加工单 ${SOURCE_MO}：${procs.length} 道工序 — ${procs.map((p) => p['工序名称']).join(' → ')}`)
  console.log(`兜底产品：${product['产品编码'] || '?'} ${product['产品名称'] || '?'}\n`)

  if (DRY) {
    log(true, '工序汇报单', '将建并审核（预演）')
    log(true, '工序报检单', '将建但【保持草稿】→ 成为卡点（预演）')
    console.log('\n===== 预演结束 =====')
    return
  }

  // 幂等：已存在就跳过
  const existing = await listOf('PROCESS_REPORT', 200)
  if (existing.find((r) => (r['加工单号'] || '') === SOURCE_MO)) {
    log(true, '工序汇报单', `已存在（挂 ${SOURCE_MO}），跳过`)
    console.log('\n===== 已造过，跳过 =====')
    return
  }

  let report
  try {
    report = await createReport(mo, procs, product)
    log(true, '工序汇报单', `${report.no}（${report.note}）`)
  } catch (e) {
    log(false, '工序汇报单', e.message)
    process.exitCode = 1
    return
  }

  try {
    const apply = await createStalledApply(report)
    log(true, '工序报检单', `${apply.no} — 状态「${apply.status}」（故意不审核）`)
  } catch (e) {
    log(false, '工序报检单', e.message)
    process.exitCode = 1
  }

  console.log('\n===== 完成：卡点样本已就绪 =====')
}

main().catch((e) => {
  console.error('异常:', e.message)
  process.exit(1)
})
