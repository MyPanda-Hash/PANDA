/**
 * 造工序链样本：工序汇报单 → 工序报检单 → 生产过程检验单 → 首件报检单（2026-11-02）
 *
 * 背景：这三张表此前 0 行，质量流程图上的工序链画了但无法被追踪验证。
 * 本脚本从 MANU_ORDER 的 processes 明细取真实工序，走 API 建出完整链条。
 *
 * 链条设计（对齐 ProcessTraceQueryService 的关联字段）：
 *   PROCESS_REPORT.加工单号      = MO 单号        → 从加工单可追到汇报单
 *   PROCESS_INSPECT_APPLY.来源单号 = PR 单号        → 从汇报单可追到报检单
 *   PROCESS_INSPECTION.来源单号   = PIA 单号       → 从报检单可追到过程检验单
 *   FIRST_INSPECT.加工单号       = MO 单号        → 首件报检也挂在加工单下
 *
 * 用法：node tools/seed-process-inspection.cjs [--dry]
 */
const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api'
const DRY = process.argv.includes('--dry')

/** 用 MO-2026-08-0007（已审核，3 道工序，计划 340 件）作为源头。 */
const SOURCE_MO = 'MO-2026-08-0007'

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

async function listOf(panelCode, pageSize = 100) {
  const r = await api('/px/queryFormDataList', { panelCode, pageNo: 1, pageSize })
  return r.code === 200 ? (r.data?.list || []) : []
}

function noOf(row) {
  return row['编号'] || row['单据编号'] || ''
}

/** 推进到已审核：草稿→审核；若变成审批中则再审批通过。 */
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

/** 取加工单的工序明细，作为汇报单的行来源。 */
async function loadProcesses() {
  const rows = await listOf('MANU_ORDER', 50)
  const mo = rows.find((r) => noOf(r) === SOURCE_MO)
  if (!mo) throw new Error('找不到加工单 ' + SOURCE_MO)
  const procs = (mo.detail?.processes || [])
  if (!procs.length) throw new Error('加工单没有工序明细')
  return { mo, procs }
}

// ---------- 1. 工序汇报单 ----------
async function createProcessReport(mo, procs) {
  const created = await button('PROCESS_REPORT', '新增流程', {})
  const autoNo = noOf(created.data || {})
  if (!autoNo) throw new Error('新增未返回编号')

  // 只汇报前两道工序（下料、铣削），第三道检验留待检验链走
  const working = procs.slice(0, 2)
  const items = working.map((p) => {
    const plan = Number(p['计划数量']) || 0
    const done = plan
    const qualified = Math.round(done * 0.97)
    return {
      加工单号: SOURCE_MO,
      产品编码: p['产品编码'] || 'CP003',
      产品名称: p['产品名称'] || '铝型材-散热片',
      规格型号: p['规格型号'] || 'XD-6063-T5',
      工艺类型: p['工艺类型'] || '自制',
      工艺序号: p['工艺序号'] || 0,
      加工顺序: p['加工顺序'] || 1,
      工序编码: p['工序编码'],
      工序名称: p['工序名称'],
      生产车间: p['生产车间'] || '精整车间',
      工序单位: p['工序单位'] || '件',
      报工数量: done,
      合格数量: qualified,
      不合格数量: done - qualified,
      工价: p['工价'] || 0,
      '计时/计件金额': Number((done * (p['工价'] || 0)).toFixed(2)),
      金额: Number((done * (p['工价'] || 0)).toFixed(2)),
      设备: p['设备'] || '',
      班组名称: p['班组'] || '',
      工人名称: p['工人'] || '',
      需求令号: mo['销售订单号'] || '',
      图号: p['图号'] || '',
    }
  })

  const saved = await button('PROCESS_REPORT', '保存', {
    编号: autoNo,
    单据日期: '2026-11-02',
    业务类型: '工序汇报',
    加工单号: SOURCE_MO,
    匹配来源单号: SOURCE_MO,
    销售订单号: mo['销售订单号'] || '',
    客户: mo['客户'] || '',
    生产车间: mo['生产车间'] || '精整车间',
    产品编码: items[0].产品编码,
    产品名称: items[0].产品名称,
    规格型号: items[0].规格型号,
    测试程序: '光谱分析',
    detail: { items },
  })
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''))

  return { no: autoNo, note: await driveToAudited('PROCESS_REPORT', autoNo), items }
}

// ---------- 2. 工序报检单（来源 = 汇报单） ----------
async function createInspectApply(report) {
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
    备注: '',
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
    检验员: '赵刚',
    来源单号: report.no,
    备注: '由工序汇报单带入',
    detail: { items },
  })
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''))

  return { no: autoNo, note: await driveToAudited('PROCESS_INSPECT_APPLY', autoNo), items }
}

// ---------- 3. 生产过程检验单（来源 = 报检单） ----------
async function createProcessInspection(apply) {
  const created = await button('PROCESS_INSPECTION', '新增流程', {})
  const autoNo = noOf(created.data || {})
  if (!autoNo) throw new Error('新增未返回编号')

  // 三态判定：合格 / 让步接收 / 报废，这里让第二行出现让步接收，便于验证 SPC 与判定
  const items = apply.items.map((it, i) => {
    const inspectQty = Number(it.报检数量) || 0
    const concession = i === 1 ? Math.round(inspectQty * 0.02) : 0
    const scrap = i === 1 ? 1 : 0
    const qualified = inspectQty - concession - scrap
    return {
      存货编码: it.存货编码,
      存货名称: it.存货名称,
      规格型号: it.规格型号,
      检验批号: `PB20261102${i + 1}`,
      仓库: '半成品仓',
      检验方式: '全检',
      '抽检比例%': 100,
      计量单位: it.计量单位,
      报检数量: inspectQty,
      检验数量: inspectQty,
      合格数量: qualified,
      让步接收数量: concession,
      报废数量: scrap,
      不合格数量: concession + scrap,
      合格率: inspectQty ? Number(((qualified / inspectQty) * 100).toFixed(2)) : 0,
      检验结果判定: scrap > 0 ? '部分报废' : (concession > 0 ? '让步接收' : '合格'),
      检验员: '赵刚',
      检验日期: '2026-11-02',
      检验项目: it.工序名称 === '下料' ? '尺寸公差' : '外观与包装',
      判定: scrap > 0 || concession > 0 ? '不合格' : '合格',
      来源面板: 'PROCESS_INSPECT_APPLY',
      来源单号: apply.no,
      来源行号: String(i + 1),
      来源数量: inspectQty,
    }
  })

  // 有不合格就要有处理记录，否则审核会被校验拦住
  const unqualified = items
    .map((it, i) => ({ it, lineNo: i + 1 }))
    .filter(({ it }) => Number(it.不合格数量) > 0)
    .map(({ it, lineNo }) => ({
      关联明细行号: lineNo,
      存货编码: it.存货编码,
      存货名称: it.存货名称,
      不合格原因: '尺寸超差',
      处理方式: '让步接收',
      处理数量: it.不合格数量,
      报废类型: '工废',
      仓库: it.仓库,
      处理说明: '批次内局部超差，评审后让步接收',
    }))

  const saved = await button('PROCESS_INSPECTION', '保存', {
    编号: autoNo,
    单据日期: '2026-11-02',
    业务类型: '工序检验',
    生产车间: apply.items[0].生产车间,
    加工单号: SOURCE_MO,
    检验员: '赵刚',
    来源单号: apply.no,
    备注: '由工序报检单带入',
    detail: { items, unqualified },
  })
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''))

  return { no: autoNo, note: await driveToAudited('PROCESS_INSPECTION', autoNo) }
}

// ---------- 4. 首件报检单（挂在加工单下） ----------
async function createFirstInspect(mo, procs) {
  const created = await button('FIRST_INSPECT', '新增流程', {})
  const autoNo = noOf(created.data || {})
  if (!autoNo) throw new Error('新增未返回编号')

  const first = procs[0]
  const inspectQty = 1
  const items = [{
    存货编码: first['产品编码'] || 'CP003',
    存货名称: first['产品名称'] || '铝型材-散热片',
    规格型号: first['规格型号'] || 'XD-6063-T5',
    工序编码: first['工序编码'],
    工序名称: first['工序名称'],
    计量单位: first['工序单位'] || '件',
    报检数量: inspectQty,
    检验数量: inspectQty,
    合格数量: inspectQty,
    不合格数量: 0,
    合格率: 100,
    检验结果判定: '合格',
    检验员: '钱丽',
    检验日期: '2026-11-02',
    检验项目: '尺寸公差',
    判定: '合格',
  }]

  const saved = await button('FIRST_INSPECT', '保存', {
    编号: autoNo,
    单据日期: '2026-11-02',
    业务类型: '首件检验',
    生产车间: first['生产车间'] || '精整车间',
    加工单号: SOURCE_MO,
    检验员: '钱丽',
    备注: '首件确认',
    detail: { items },
  })
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''))

  return { no: autoNo, note: await driveToAudited('FIRST_INSPECT', autoNo) }
}

async function main() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' })
  if (login.code !== 200) {
    console.log('登录失败:', JSON.stringify(login).slice(0, 200))
    process.exit(1)
  }
  token = login.data.token
  console.log(`===== 造工序链样本${DRY ? '（预演）' : ''} =====\n`)

  const { mo, procs } = await loadProcesses()
  console.log(`源加工单 ${SOURCE_MO}：${procs.length} 道工序 — ${procs.map((p) => p['工序名称']).join(' → ')}\n`)

  if (DRY) {
    log(true, '工序汇报单', `将汇报 ${Math.min(2, procs.length)} 道工序（预演）`)
    log(true, '工序报检单', '来源=汇报单（预演）')
    log(true, '生产过程检验单', '来源=报检单，含让步接收（预演）')
    log(true, '首件报检单', `挂 ${SOURCE_MO}（预演）`)
    console.log('\n===== 预演结束 =====')
    return
  }

  let failed = 0

  // 已存在就跳过，保证可重复执行
  const existing = await listOf('PROCESS_REPORT', 200)
  const already = existing.find((r) => (r['加工单号'] || '') === SOURCE_MO)
  if (already) {
    log(true, '工序汇报单', `已存在 ${noOf(already)}，跳过`)
    console.log('\n===== 已造过，跳过 =====')
    return
  }

  let report
  try {
    report = await createProcessReport(mo, procs)
    log(true, '工序汇报单', `${report.no}（${report.note}）`)
  } catch (e) {
    log(false, '工序汇报单', e.message)
    process.exitCode = 1
    return
  }

  let apply
  try {
    apply = await createInspectApply(report)
    log(true, '工序报检单', `${apply.no}（${apply.note}）`)
  } catch (e) {
    log(false, '工序报检单', e.message)
    failed++
  }

  if (apply) {
    try {
      const insp = await createProcessInspection(apply)
      log(true, '生产过程检验单', `${insp.no}（${insp.note}）`)
    } catch (e) {
      log(false, '生产过程检验单', e.message)
      failed++
    }
  }

  try {
    const fi = await createFirstInspect(mo, procs)
    log(true, '首件报检单', `${fi.no}（${fi.note}）`)
  } catch (e) {
    log(false, '首件报检单', e.message)
    failed++
  }

  console.log(`\n===== 失败 ${failed} 项 =====`)
  if (failed) process.exitCode = 1
}

main().catch((e) => {
  console.error('异常:', e.message)
  process.exit(1)
})
