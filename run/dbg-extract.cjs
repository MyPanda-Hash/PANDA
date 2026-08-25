const fs = require('fs');
const text = fs.readFileSync('F:/INCER/light-mes/docs/deploy/local-panels.sql', 'utf8');
function extract(code) {
  const marker = `'${code}','`;
  let i = text.indexOf(marker);
  if (i < 0) return null;
  const start = text.indexOf('{', i);
  const end = text.indexOf("}','", start);
  if (end < 0) return null;
  const raw = text.slice(start, end + 1);
  return JSON.parse('"' + raw.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"');
}
const j = extract('MATERIAL_REQ');
console.log('top keys:', Object.keys(j));
console.log('metadata keys:', j.metadata ? Object.keys(j.metadata) : 'none');
console.log('panelPageDto:', JSON.stringify(j.metadata?.panelPageDto ? Object.keys(j.metadata.panelPageDto) : 'none'));
console.log('has selectConfig:', 'selectConfig' in j);
console.log('selectConfig:', JSON.stringify(j.selectConfig));