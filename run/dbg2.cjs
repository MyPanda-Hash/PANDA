const fs = require('fs');
const s = fs.readFileSync('F:/INCER/light-mes/tools/update-transfer-pureq-analysis.sql', 'utf8');
const i = s.indexOf("'TRANSFER', '");
const start = s.indexOf('{', i);
const end = s.indexOf("}','", start);
console.log('start', start, 'end', end, 'len', s.length);
const raw = s.slice(start, end + 1);
console.log('raw tail:', JSON.stringify(raw.slice(-120)));
console.log('raw head:', JSON.stringify(raw.slice(0, 80)));