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
  // SQL dump escapes quotes as \" and backslashes as \\; unescape once.
  return JSON.parse('"' + raw.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"');
}
for (const code of ['PICK_ORDER', 'MATERIAL_REQ', 'SALE_INV', 'DISPATCH', 'PU_ORDER', 'PURCHASE_IN']) {
  try {
    const j = extract(code);
    const tp = j.metadata?.panelPageDto?.tablePages?.[0];
    const fp = j.metadata?.panelPageDto?.formPages?.[0];
    const out = {
      panelName: j.metadata?.panelName,
      category: j.metadata?.panelCategory,
      stateField: j.metadata?.panelState,
      singleDoc: j.metadata?.singleDoc,
      topBarBtn: tp?.topBarBtn?.map(b => b.buttonName),
      buttonGroups: j.metadata?.buttonGroups,
      queryFields: tp?.queryFields,
      gridTabs: tp?.gridTabs,
      formFields: fp?.fieldNames,
      detailTabs: j.detail?.tabs?.map(t => ({ key: t.key, label: t.label, fieldCount: t.fields?.length })),
      selectConfig: j.selectConfig ? { title: j.selectConfig.title, source: j.selectConfig.sourcePanel } : null,
      dataSchemaFields: j.dataSchema?.fields?.map(f => f.dataName),
    };
    fs.writeFileSync('F:/INCER/light-mes/run/panel-summary-' + code + '.json', JSON.stringify(out, null, 1), 'utf8');
    console.log(code + ': OK');
  } catch (e) {
    console.log(code + ': PARSE FAIL ' + e.message);
  }
}