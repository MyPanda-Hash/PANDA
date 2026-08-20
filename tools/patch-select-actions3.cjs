const fs = require('fs');
const src = fs.readFileSync('frontend/src/business/engine.js', 'utf8');
const lines = src.split('\n');

const targets = {
  PURCHASE_IN: ['选单', '选采购订单'],
  FINISH_IN: ['选单', '选生产加工单'],
  OTHER_IN: ['选单', '选其他出库单'],
  SALE_OUT: ['选单', '选销售订单'],
  MATERIAL_OUT: ['选单', '选生产加工单'],
  OTHER_OUT: ['选单', '选配货单'],
};

let out = src;
let report = [];
for (const [code, want] of Object.entries(targets)) {
  const startIdx = lines.findIndex(l => l.startsWith(`const ${code}_CONFIG`));
  if (startIdx < 0) { report.push(`${code}: NOT FOUND`); continue; }
  let endIdx = lines.findIndex((l, i) => i > startIdx && /^const [A-Z_]+(_CONFIG|_ROWS|_SEED)?\s*=/.test(l));
  if (endIdx < 0) endIdx = lines.length;
  // 段文本（用行级操作替换）
  const seg = lines.slice(startIdx, endIdx);
  // 找 "name": "选单" 组（双引号 JSON 格式）
  const gi = seg.findIndex(l => l.includes('"name": "选单"'));
  if (gi < 0) {
    // 插入到 toolbarDiff: [ 之后
    const ti = seg.findIndex(l => l.includes('toolbarDiff: ['));
    if (ti < 0) { report.push(`${code}: 无 toolbarDiff 也无选单组`); continue; }
    const indent = seg[ti].match(/^(\s*)/)[1] + '      ';
    const item = [
      indent + '{',
      indent + '  "name": "选单",',
      indent + '  "actions": ' + JSON.stringify(want) + ',',
      indent + '},',
    ];
    seg.splice(ti + 1, 0, ...item);
    report.push(`${code}: 插入选单组 ${JSON.stringify(want)}`);
  } else {
    // 替换该组 actions
    let ai = gi;
    while (ai < seg.length && !seg[ai].includes('"actions"')) ai++;
    if (ai >= seg.length) { report.push(`${code}: 选单组无 actions`); continue; }
    seg[ai] = seg[ai].replace(/\[[^\]]*\]/, JSON.stringify(want));
    // 删除旧组中 actions 之后的残留行（如 "智能选单" 行）直到 "}," 或 "},"
    let j = ai + 1;
    while (j < seg.length && !/^\s*\},?\s*$/.test(seg[j])) {
      seg.splice(j, 1);
    }
    report.push(`${code}: 替换选单组 actions -> ${JSON.stringify(want)}`);
  }
  out = out.split('\n');
  out.splice(startIdx, seg.length, ...seg);
  out = out.join('\n');
}
fs.writeFileSync('frontend/src/business/engine.js', out);
console.log(report.join('\n'));
try { new Function(out.replace(/export default/, 'return')); console.log('语法 OK'); } catch (e) { console.log('语法错误: ' + e.message); }