const BASE = 'http://127.0.0.1:8080/api';
async function main() {
  const login = await fetch(BASE + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ userName: 'admin', password: '123456' }) }).then(r => r.json());
  const token = login.data.token;
  for (const [code, tabKey] of [['OUTSOURCE_ORDER', 'products'], ['OUTSOURCE_ORDER', 'materials'], ['OUTSOURCE_ISSUE', 'items'], ['OUTSOURCE_IN', 'items'], ['TRANSFER', 'items'], ['QUOTE_ORDER', 'items'], ['SALE_INVOICE', 'items'], ['PU_INVOICE', 'items'], ['STOCK_CHECK', 'items'], ['LOCATION_ADJUST', 'items'], ['MANU_ORDER', 'materials']]) {
    const cfg = await fetch(BASE + '/px/getPanelConfig?panelCode=' + code, { headers: { 'Authorization': 'Bearer ' + token } }).then(r => r.json());
    const tab = (cfg?.data?.detail?.tabs || []).find(t => t.key === tabKey);
    if (!tab) { console.log(code, tabKey, ': no tab'); continue; }
    const withMap = tab.fields.filter(f => f.refMap).map(f => f.dataName + '→' + f.refMap.map(m => m.from + '>' + m.to).join(','));
    console.log(code, tabKey + ':', withMap.length ? withMap.join(' | ') : 'NO refMap');
  }
}
main().catch(e => { console.error('ERR', e.message); process.exit(1); });