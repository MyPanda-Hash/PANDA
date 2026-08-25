const { execSync } = require('child_process');
const env = { ...process.env, MYSQL_PWD: '000518' };
const mysql = 'D:/phpstudy_pro/Extensions/MySQL8.0.12/bin/mysql.exe';
function run(sql) {
  return execSync(`"${mysql}" --host=127.0.0.1 --port=3308 --user=root --ssl-mode=DISABLED --default-character-set=utf8mb4 --database=light_mes --batch --skip-column-names --execute="${sql}"`, { env, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
}
// INV 物品行 _bom 检查
const invDocs = run(`SELECT data, detail_data FROM form_data WHERE panel_code='INV'`).trim().split('\n').filter(Boolean);
let invWithBom = 0, invItems = 0;
const samples = [];
for (const raw of invDocs) {
  try {
    const d = JSON.parse(raw.split('\t')[1]);
    for (const it of (d.items || [])) {
      invItems++;
      if (it['_bom']) { invWithBom++; if (samples.length < 3) samples.push(it['存货编码'] + ' → ' + JSON.stringify(it['_bom']).slice(0, 150)); }
    }
  } catch {}
}
console.log('INV 物品行:', invItems, '| 带 _bom 的行:', invWithBom);
samples.forEach(s => console.log('  ', s));
// BOM 面板 children 结构
const boms = run(`SELECT form_no, detail_data FROM form_data WHERE panel_code='BOM'`).trim().split('\n').filter(Boolean);
console.log('BOM 面板单据数:', boms.length);
if (boms[0]) {
  const d = JSON.parse(boms[0].split('\t')[1]);
  const ch = (d.children || [])[0] || {};
  console.log('children 首行字段:', Object.keys(ch).join(','));
  console.log('  样例:', JSON.stringify(ch).slice(0, 250));
}