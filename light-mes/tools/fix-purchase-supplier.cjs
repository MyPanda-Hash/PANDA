/**
 * 给采购入库/退货单补供应商（2026-11-02）
 *
 * 现状：RK-2026-11-0201/0202/0203 与 CT-2026-11-0201 的「供应商」为空，
 * 导致 FinanceSettleService 生成的 AP 往来单 partner 为空，
 * 供应商对账单与账龄表无法按单位归集，报表只看得到"未指定"一行。
 *
 * 主数据里 PARTNER-006 = GYS001 华东热处理厂（性质=供应商），回填到这些单据。
 *
 * 用法：node tools/fix-purchase-supplier.cjs [--dry]
 */
const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api';
const DRY = process.argv.includes('--dry');

const SUPPLIER = { 供应商编码: 'GYS001', 供应商: '华东热处理厂', 供应商简称: '华东热处理厂' };

const TARGETS = [
  { panel: 'PURCHASE_IN', no: 'RK-2026-11-0201' },
  { panel: 'PURCHASE_IN', no: 'RK-2026-11-0202' },
  { panel: 'PURCHASE_IN', no: 'RK-2026-11-0203' },
  { panel: 'PURCHASE_RETURN', no: 'CT-2026-11-0201' },
];

let token = null;

function log(ok, name, extra = '') {
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
  return (r.data?.list || []).find((x) => (x['编号'] || x['单据编号']) === formNo) || null;
}

async function driveToAudited(panel, no) {
  const doc = await findDoc(panel, no);
  const status = doc?.['单据状态'] || '';
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

async function fixOne(spec) {
  const doc = await findDoc(spec.panel, spec.no);
  if (!doc) throw new Error('单据不存在');
  if (doc['供应商'] === SUPPLIER.供应商) return '供应商已正确';

  const status = doc['单据状态'] || '';
  if (status === '已审核') {
    const un = await button(spec.panel, '弃审', { 编号: spec.no });
    if (un.code !== 200) throw new Error('弃审: ' + (un.message || ''));
  }

  // 关键：保存是整体覆盖表头，必须带上原有字段，否则单据日期/业务类型会被清空
  const head = {};
  for (const [key, value] of Object.entries(doc)) {
    if (key === 'detail' || key === 'meta') continue;
    if (key === '单据状态' || key === '创建时间' || key === '更新时间') continue;
    if (key === '审核人' || key === '审核时间' || key === '审核意见') continue;
    if (key === '审批状态' || key === '提交人' || key === '提交时间') continue;
    head[key] = value;
  }
  Object.assign(head, SUPPLIER, { 编号: spec.no });

  const saved = await button(spec.panel, '保存', head);
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''));

  return `${SUPPLIER.供应商}，${await driveToAudited(spec.panel, spec.no)}`;
}

async function main() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' });
  if (login.code !== 200) {
    console.log('登录失败:', JSON.stringify(login).slice(0, 200));
    process.exit(1);
  }
  token = login.data.token;
  console.log(`===== 补采购单供应商${DRY ? '（预演）' : ''} =====\n`);

  let done = 0;
  let failed = 0;
  for (const spec of TARGETS) {
    if (DRY) {
      log(true, `${spec.panel}/${spec.no}`, `→ ${SUPPLIER.供应商}（预演）`);
      done++;
      continue;
    }
    try {
      log(true, `${spec.panel}/${spec.no}`, await fixOne(spec));
      done++;
    } catch (e) {
      log(false, `${spec.panel}/${spec.no}`, e.message);
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
