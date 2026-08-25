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
];
for (const f of files) {
  const j = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
  console.log('\n===== ' + f + ' =====');
  console.log(j.body);
}