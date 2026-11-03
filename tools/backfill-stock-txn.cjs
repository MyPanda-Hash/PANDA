/**
 * 把历史已审核出入库单补齐库存事务（2026-11-02）
 *
 * 背景：种子单据由 SQL 直插 form_data，绕过了 PxService 的落账逻辑，
 * 所以 stock_txn / stock_balance 只有零星几行。本脚本走真实 API，
 * 让系统按正式路径重建事务与余额。
 *
 * 关键坑：APPROVAL_PANELS 里的面板（INIT_BALANCE/PURCHASE_IN/SALE_OUT/MATERIAL_OUT 等）
 * 「审核」只是提交审批 → 状态变「审批中」，不落账；必须再调「审批通过」才落库存事务。
 * 只调「审核」会把旧事务冲销掉却不重建，余额表反而变空。
 *
 * 用法：node tools/backfill-stock-txn.cjs [--dry]
 */
const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api';
const DRY = process.argv.includes('--dry');

const STOCK_PANELS = [
  'INIT_BALANCE', 'PURCHASE_IN', 'FINISH_IN', 'OTHER_IN', 'OUTSOURCE_IN',
  'SALE_RETURN', 'SALE_OUT', 'MATERIAL_OUT', 'OTHER_OUT', 'PURCHASE_RETURN',
];

let token = null;
const results = [];

function log(ok, name, extra = '') {
  results.push({ ok, name, extra });
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);
}

async function api(path, body) {
  const res = await fetch(BASE + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: 'Bearer ' + token } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

async function button(panelCode, buttonName, formData = {}) {
  return api('/px/callButton', { panelCode, buttonName, formData, buttonParam: {} });
}

async function listAll(panelCode) {
  const r = await api('/px/queryFormDataList', { panelCode, pageNo: 1, pageSize: 500 });
  if (r.code !== 200) throw new Error(`${panelCode} 查询失败: ${r.message || JSON.stringify(r).slice(0, 120)}`);
  return r.data?.list || r.data?.rows || r.data?.records || [];
}

function noOf(row) {
  return row['编号'] || row['单据编号'] || row.formNo;
}

function statusOf(row) {
  return row['单据状态'] || row.status || '';
}

/** 把一张单据推进到「已审核」：草稿→审核，审批中→审批通过，已审核→直接算完成。 */
async function driveToAudited(panel, no, status) {
  if (status === '已审核') return { ok: true, note: '已是已审核' };

  if (status === '审批中') {
    const approved = await button(panel, '审批通过', { 编号: no });
    if (approved.code !== 200) return { ok: false, note: '审批通过: ' + (approved.message || '') };
    return { ok: true, note: '审批通过→已审核（落账）' };
  }

  // 草稿（或其它可审核状态）：先审核
  const audited = await button(panel, '审核', { 编号: no });
  if (audited.code !== 200) return { ok: false, note: '审核: ' + (audited.message || '') };

  const after = String(audited.data?.['单据状态'] || audited.data?.status || '');
  if (after === '已审核') return { ok: true, note: '审核→已审核（落账）' };
  if (after === '审批中') {
    const approved = await button(panel, '审批通过', { 编号: no });
    if (approved.code !== 200) return { ok: false, note: '审批通过: ' + (approved.message || '') };
    return { ok: true, note: '审核→审批中→审批通过（落账）' };
  }
  return { ok: false, note: '审核后状态未识别: ' + after };
}

async function main() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' });
  if (login.code !== 200) {
    console.log('登录失败:', JSON.stringify(login).slice(0, 200));
    process.exit(1);
  }
  token = login.data.token;
  console.log(`===== 库存事务补录${DRY ? '（预演）' : ''} =====\n`);

  let done = 0;
  let failed = 0;
  for (const panel of STOCK_PANELS) {
    let rows;
    try {
      rows = await listAll(panel);
    } catch (e) {
      log(false, `${panel} 查询`, e.message);
      failed++;
      continue;
    }
    for (const row of rows) {
      const no = noOf(row);
      const status = statusOf(row);
      if (!no) {
        log(false, `${panel} 取号`, JSON.stringify(row).slice(0, 100));
        failed++;
        continue;
      }
      if (status === '草稿' || status === '') continue; // 草稿不落账，跳过
      if (DRY) {
        log(true, `${panel}/${no}`, `当前「${status}」（预演，不执行）`);
        done++;
        continue;
      }
      const r = await driveToAudited(panel, no, status);
      log(r.ok, `${panel}/${no}`, r.note);
      if (r.ok) done++; else failed++;
    }
  }

  console.log(`\n===== 完成 ${done} 张，失败 ${failed} 张 =====`);
  if (failed) process.exitCode = 1;
}

main().catch((e) => {
  console.error('异常:', e.message);
  process.exit(1);
});
