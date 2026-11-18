/**
 * 补齐缺失期初 + 修正存货名称错配（2026-11-02）
 *
 * 三处缺口：
 * 1. 期初单 IB-001 只覆盖 CL002/CL001/CL004，IB-002 只覆盖 CP001/CP002。
 *    CL003（6063铝棒）、CL005（包装木箱）没有期初，但 8/25 领料单已出库，
 *    导致 stock_balance 出现 -411.4 / -6.8 的无源负库存。这里补 IB-003/IB-004 期初。
 * 2. PURCHASE_IN 三张单 + PURCHASE_RETURN 的 CL001 存货名称写成「铝棒」，
 *    而存货档案 CL001 = 45#圆钢。名称错配会让现存量查询按名称显示错误，
 *    统一按档案名回填。
 *
 * 用法：node tools/fix-stock-data.cjs [--dry]
 */
const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api';
const DRY = process.argv.includes('--dry');

/** 期望的期初：期初单号 → 仓库 + 明细 */
const INITIAL_BALANCES = [
  {
    no: 'IB-2026-08-0003',
    date: '2026-08-01',
    remark: '期初建账-铝棒与辅料',
    rows: [
      { 存货编码: 'CL003', 存货名称: '6063铝棒', 规格型号: 'Φ120', 计量单位: 'kg', 数量: 2000, 主单价: 13.5, 仓库: '原料仓' },
      { 存货编码: 'CL005', 存货名称: '包装木箱', 规格型号: '1200×800', 计量单位: '件', 数量: 500, 主单价: 5, 仓库: '辅料仓' },
    ],
  },
];

/** 名称错配修正：面板 → 单号 → 行内编码→正确名称 */
const NAME_FIXES = [
  { panel: 'PURCHASE_IN', no: 'RK-2026-11-0201', code: 'CL001', name: '45#圆钢' },
  { panel: 'PURCHASE_IN', no: 'RK-2026-11-0202', code: 'CL001', name: '45#圆钢' },
  { panel: 'PURCHASE_IN', no: 'RK-2026-11-0203', code: 'CL001', name: '45#圆钢' },
  { panel: 'PURCHASE_RETURN', no: 'CT-2026-11-0201', code: 'CL001', name: '45#圆钢' },
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
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

async function button(panelCode, buttonName, formData = {}) {
  return api('/px/callButton', { panelCode, buttonName, formData, buttonParam: {} });
}

async function findDoc(panelCode, formNo) {
  const r = await api('/px/queryFormDataList', { panelCode, pageNo: 1, pageSize: 500 });
  const rows = r.data?.list || [];
  return rows.find((x) => (x['编号'] || x['单据编号']) === formNo) || null;
}

/** 推进到已审核：草稿→审核；审批中→审批通过 */
async function driveToAudited(panel, no) {
  const doc = await findDoc(panel, no);
  if (!doc) throw new Error('单据不存在');
  const status = doc['单据状态'] || '';
  if (status === '已审核') return '已是已审核';

  if (status === '审批中') {
    const p = await button(panel, '审批通过', { 编号: no });
    if (p.code !== 200) throw new Error('审批通过: ' + (p.message || ''));
    return '审批通过→已审核';
  }
  const a = await button(panel, '审核', { 编号: no });
  if (a.code !== 200) throw new Error('审核: ' + (a.message || ''));
  if (String(a.data?.['单据状态'] || '') === '审批中') {
    const p = await button(panel, '审批通过', { 编号: no });
    if (p.code !== 200) throw new Error('审批通过: ' + (p.message || ''));
    return '审核→审批中→审批通过';
  }
  return '审核→已审核';
}

async function createInitialBalance(spec) {
  const created = await button('INIT_BALANCE', '新增流程', {});
  const autoNo = created.data?.['编号'] || created.data?.['单据编号'];
  if (!autoNo) throw new Error('新增未返回编号: ' + JSON.stringify(created).slice(0, 140));

  const items = spec.rows.map((row) => ({
    存货编码: row.存货编码,
    存货名称: row.存货名称,
    规格型号: row.规格型号,
    计量单位: row.计量单位,
    数量: row.数量,
    主单价: row.主单价,
    金额: Number((row.数量 * row.主单价).toFixed(2)),
    仓库: row.仓库,
    制单人: 'admin',
    审核人: '系统管理员',
  }));

  const saved = await button('INIT_BALANCE', '保存', {
    编号: autoNo,
    单据日期: spec.date,
    业务类型: '期初建账',
    仓库: spec.rows[0].仓库,
    备注: spec.remark,
    detail: { items },
  });
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''));

  const note = await driveToAudited('INIT_BALANCE', autoNo);
  return `${autoNo}（${note}）`;
}

async function fixName(spec) {
  const doc = await findDoc(spec.panel, spec.no);
  if (!doc) throw new Error('单据不存在');
  const status = doc['单据状态'] || '';
  const items = doc.detail?.items || [];
  const target = items.filter((it) => it['存货编码'] === spec.code);
  if (target.length === 0) return '无该编码明细，跳过';
  if (target.every((it) => it['存货名称'] === spec.name)) return '名称已正确';

  // 已审核单据必须先弃审才能改
  if (status === '已审核') {
    const un = await button(spec.panel, '弃审', { 编号: spec.no });
    if (un.code !== 200) throw new Error('弃审: ' + (un.message || ''));
  }

  const fixed = items.map((it) =>
    it['存货编码'] === spec.code ? { ...it, 存货名称: spec.name } : it,
  );
  const saved = await button(spec.panel, '保存', { 编号: spec.no, detail: { items: fixed } });
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''));

  const note = await driveToAudited(spec.panel, spec.no);
  return `${target.length} 行改名为「${spec.name}」，${note}`;
}

async function main() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' });
  if (login.code !== 200) {
    console.log('登录失败:', JSON.stringify(login).slice(0, 200));
    process.exit(1);
  }
  token = login.data.token;
  console.log(`===== 补齐期初 + 修正名称${DRY ? '（预演）' : ''} =====\n`);

  let done = 0;
  let failed = 0;

  for (const spec of INITIAL_BALANCES) {
    const existing = await findDoc('INIT_BALANCE', spec.no);
    if (existing) {
      log(true, `期初 ${spec.no}`, '已存在，跳过');
      continue;
    }
    if (DRY) {
      log(true, `期初 ${spec.no}`, `${spec.rows.length} 行（预演）`);
      done++;
      continue;
    }
    try {
      log(true, `期初 ${spec.no}`, await createInitialBalance(spec));
      done++;
    } catch (e) {
      log(false, `期初 ${spec.no}`, e.message);
      failed++;
    }
  }

  for (const spec of NAME_FIXES) {
    if (DRY) {
      log(true, `${spec.panel}/${spec.no} 改名`, `${spec.code} → ${spec.name}（预演）`);
      done++;
      continue;
    }
    try {
      log(true, `${spec.panel}/${spec.no} 改名`, await fixName(spec));
      done++;
    } catch (e) {
      log(false, `${spec.panel}/${spec.no} 改名`, e.message);
      failed++;
    }
  }

  console.log(`\n===== 完成 ${done}，失败 ${failed} =====`);
  if (failed) process.exitCode = 1;
}

main().catch((e) => {
  console.error('异常:', e.message);
  process.exit(1);
});
