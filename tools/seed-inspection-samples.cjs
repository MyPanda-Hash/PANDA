/**
 * 补检验样本让 SPC 控制图可判断（2026-11-02）
 *
 * 现状：INSPECTION 只有 2 张单，已审核的仅 1 张 3 行，SPC_CONTROL 每项目样本数=1、
 * σ=0、控制限退化成一个点，控制图没有判断力。
 *
 * 本脚本走真实 API 建 8 批检验单（不同检验日期/批号/合格率），审核后
 * SpcQueryService 按检验项目汇总，样本数≥8，σ≠0，控制限才能张开。
 *
 * 注意：检验单在 APPROVAL_PANELS 内，「审核」只到审批中，必须再「审批通过」才落终态。
 *
 * 用法：node tools/seed-inspection-samples.cjs [--dry]
 */
const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api';
const DRY = process.argv.includes('--dry');

/** 8 批样本：合格率刻意有波动，才能算出台标准差与上下控制限。 */
const BATCHES = [
  { no: 'JY-2026-11-1001', date: '2026-11-01', rate: 99.2, inspector: '赵刚', level: '来料检验' },
  { no: 'JY-2026-11-1002', date: '2026-11-02', rate: 98.6, inspector: '赵刚', level: '来料检验' },
  { no: 'JY-2026-11-1003', date: '2026-11-03', rate: 97.4, inspector: '钱丽', level: '来料检验' },
  { no: 'JY-2026-11-1004', date: '2026-11-04', rate: 99.6, inspector: '钱丽', level: '成品检验' },
  { no: 'JY-2026-11-1005', date: '2026-11-05', rate: 96.8, inspector: '孙鹏', level: '成品检验' },
  { no: 'JY-2026-11-1006', date: '2026-11-06', rate: 99.0, inspector: '孙鹏', level: '成品检验' },
  { no: 'JY-2026-11-1007', date: '2026-11-07', rate: 98.2, inspector: '赵刚', level: '来料检验' },
  { no: 'JY-2026-11-1008', date: '2026-11-08', rate: 97.8, inspector: '钱丽', level: '成品检验' },
];

/** 三个检验项目，各自稳定波动，SPC 才有分组意义。 */
const ITEMS = [
  { name: '尺寸公差', base: -0.4 },
  { name: '化学成分', base: -1.1 },
  { name: '外观与包装', base: -0.8 },
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

/** 建一张检验单并推进到已审核（审核→审批中→审批通过） */
async function createInspected(batch, index) {
  const created = await button('INSPECTION', '新增流程', {});
  const autoNo = created.data?.['编号'] || created.data?.['单据编号'];
  if (!autoNo) throw new Error('新增未返回编号: ' + JSON.stringify(created).slice(0, 140));

  // 明细：三个检验项目各一行，报检/检验数量固定，合格数量按 batch.rate 波动
  const inspectQty = 100;
  const rows = ITEMS.map((item, i) => {
    const rate = Number((batch.rate + item.base + ((index % 3) - 1) * 0.3).toFixed(2));
    const qualified = Number(((inspectQty * rate) / 100).toFixed(2));
    const unqualified = Number((inspectQty - qualified).toFixed(2));
    return {
      存货编码: 'CP001',
      存货名称: '铝棒 Φ80',
      规格型号: 'Φ80×3000',
      检验批号: `B${batch.date.replace(/-/g, '')}${i + 1}`,
      仓库: '成品仓',
      检验方式: '全检',
      '抽检比例%': 100,
      计量单位: '件',
      报检数量: inspectQty,
      检验数量: inspectQty,
      合格数量: qualified,
      不合格数量: unqualified,
      合格率: rate,
      检验结果判定: unqualified === 0 ? '合格' : '让步接收',
      检验员: batch.inspector,
      检验日期: batch.date,
      检验项目: item.name,
      判定: unqualified === 0 ? '合格' : '不合格',
    };
  });

  // 审核校验要求：有不合格数量时，「不合格处理」明细必须有一条处理数量相等的记录（按行号关联）
  const treatments = rows
    .map((row, i) => ({ row, lineNo: i + 1 }))
    .filter(({ row }) => Number(row.不合格数量) > 0)
    .map(({ row, lineNo }) => ({
      关联明细行号: lineNo,
      存货编码: row.存货编码,
      存货名称: row.存货名称,
      不合格原因: '尺寸超差',
      处理方式: '让步接收',
      处理数量: row.不合格数量,
      报废类型: '工废',
      仓库: row.仓库,
      处理说明: '批次内局部超差，评审后让步接收',
    }));

  const saved = await button('INSPECTION', '保存', {
    编号: autoNo,
    单据编号: autoNo,
    单据日期: batch.date,
    业务类型: batch.level,
    检验类别: batch.level,
    仓库: '成品仓',
    检验员: batch.inspector,
    detail: { items: rows, unqualified: treatments },
  });
  if (saved.code !== 200) throw new Error('保存失败: ' + (saved.message || ''));

  const audited = await button('INSPECTION', '审核', { 编号: autoNo });
  if (audited.code !== 200) throw new Error('审核失败: ' + (audited.message || ''));
  const st = String(audited.data?.['单据状态'] || '');
  if (st === '审批中') {
    const approved = await button('INSPECTION', '审批通过', { 编号: autoNo });
    if (approved.code !== 200) throw new Error('审批通过失败: ' + (approved.message || ''));
  }
  return autoNo;
}

async function main() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' });
  if (login.code !== 200) {
    console.log('登录失败:', JSON.stringify(login).slice(0, 200));
    process.exit(1);
  }
  token = login.data.token;
  console.log(`===== 补检验样本${DRY ? '（预演）' : ''} =====\n`);

  let done = 0;
  let failed = 0;
  for (let i = 0; i < BATCHES.length; i++) {
    const batch = BATCHES[i];
    if (DRY) {
      log(true, `${batch.no}`, `${batch.date} 合格率≈${batch.rate}（预演）`);
      done++;
      continue;
    }
    try {
      const no = await createInspected(batch, i);
      log(true, `${batch.no}`, `已建合并审核 → ${no}`);
      done++;
    } catch (e) {
      log(false, `${batch.no}`, e.message);
      failed++;
    }
  }

  console.log(`\n===== 完成 ${done} 张，失败 ${failed} 张 =====`);
  if (failed) process.exitCode = 1;
}

main().catch((e) => {
  console.error('异常:', e.message);
  process.exit(1);
});
