const fs = require('fs');
const s = fs.readFileSync('F:/INCER/light-mes/tools/update-transfer-pureq-analysis.sql', 'utf8');
for (const code of ['TRANSFER', 'PU_REQ_ANALYSIS']) {
  const marker = `'${code}', '`;
  const i = s.indexOf(marker);
  const start = s.indexOf('{', i);
  const ver = s.indexOf("', '1.0'", start);
  const raw = s.slice(start, ver);
  try {
    const cfg = JSON.parse(raw.replace(/\\"/g, '"'));
    const tp = cfg.metadata.panelPageDto.tablePages[0];
    console.log(code, '->', cfg.metadata.panelName, '| tabs:', tp.gridTabs.map(g => g.label).join(','), '| fields:', cfg.dataSchema.fields.length, '| selectConfig:', cfg.selectConfig ? cfg.selectConfig.source : '-');
  } catch (e) {
    console.log(code, 'PARSE FAIL', e.message, 'rawlen', raw.length);
  }
}