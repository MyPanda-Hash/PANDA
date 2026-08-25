const fs = require('fs');
const path = require('path');
const dir = 'F:/INCER/light-mes/docs/ref/tplus-live/business-overview-20260824';
const files = [
  'production-relation.json',
  'outsource-relation.json',
  'sales-relation.json',
  'purchase-relation.json',
  'distribution-relation.json',
  'mobile-warehouse-relation.json',
  'serial-number-relation.json',
  'isc-idesk.json',
];
function walk(v, depth, out) {
  if (depth > 3) return;
  if (Array.isArray(v)) {
    if (v.length && typeof v[0] === 'object' && v[0] !== null) {
      out.push('  '.repeat(depth) + '[] items: ' + Object.keys(v[0]).join(', '));
      walk(v[0], depth + 1, out);
    } else if (v.length) {
      out.push('  '.repeat(depth) + '[] ' + v.length + ' scalar items: ' + JSON.stringify(v.slice(0, 5)));
    }
    return;
  }
  if (v && typeof v === 'object') {
    for (const k of Object.keys(v)) {
      const val = v[k];
      if (typeof val === 'string') out.push('  '.repeat(depth) + k + ': "' + (val.length > 60 ? val.slice(0, 60) + '…' : val) + '"');
      else if (typeof val === 'number' || typeof val === 'boolean') out.push('  '.repeat(depth) + k + ': ' + val);
      else walk(val, depth + 1, out);
    }
  }
}
for (const f of files) {
  const p = path.join(dir, f);
  if (!fs.existsSync(p)) { console.log('===== ' + f + ' MISSING ====='); continue; }
  const j = JSON.parse(fs.readFileSync(p, 'utf8'));
  console.log('\n===== ' + f + ' =====');
  const out = [];
  walk(j, 0, out);
  console.log(out.slice(0, 120).join('\n'));
}
