const fs = require('fs');
const p = 'F:/INCER/light-mes/frontend/src/core/views/PanelxList.vue';
let s = fs.readFileSync(p, 'utf8');
const edits = [
  // 模板：明细区块左上角「已审批」角标
  ['      <div class="detail" v-for="b in blocks" :key="b.id">\n        <div class="dt-head">',
   '      <div class="detail" v-for="b in blocks" :key="b.id">\n        <div v-if="isApproved" class="approved-stamp">已审批</div>\n        <div class="dt-head">'],
  // script：isApproved + rowCls 补审批行高亮
  ['function rowCls({ row }) {\n  if (row._placeholder) return \'ph-row\'\n  return [\'产品编码\', \'材料编码\', \'存货编码\', \'存货名称\', \'产品名称\', \'材料名称\'].some((k) => row[k] === \'合计\') ? \'sum-row\' : \'\'\n}',
   '// 审批流：当前单据已审批 → 表格左上角「已审批」角标；已审批明细行浅绿底色\nconst isApproved = computed(() => cur.value && cur.value[\'审批状态\'] === \'已审批\')\n\nfunction rowCls({ row }) {\n  if (row._placeholder) return \'ph-row\'\n  if (row[\'审批状态\'] === \'已审批\') return \'row-approved\'\n  return [\'产品编码\', \'材料编码\', \'存货编码\', \'存货名称\', \'产品名称\', \'材料名称\'].some((k) => row[k] === \'合计\') ? \'sum-row\' : \'\'\n}'],
  // 样式
  ['.detail {\n  border: 1px solid #d7dce5;\n  margin-bottom: 8px;\n  background: #fff;\n}',
   `.detail {\n  border: 1px solid #d7dce5;\n  margin-bottom: 8px;\n  background: #fff;\n  position: relative;\n}\n.approved-stamp {\n  position: absolute;\n  top: 3px;\n  left: 6px;\n  z-index: 9;\n  transform: rotate(-12deg);\n  color: #16a34a;\n  border: 2px solid #16a34a;\n  border-radius: 4px;\n  padding: 0 10px;\n  font-size: 14px;\n  font-weight: 700;\n  background: rgba(240, 253, 244, 0.92);\n  pointer-events: none;\n  letter-spacing: 3px;\n  box-shadow: 0 1px 3px rgba(22, 163, 74, 0.25);\n}`],
  ['</style>',
   '.el-table .row-approved td { background: #f0fdf4 !important; }\n</style>'],
];
for (const [oldStr, newStr] of edits) {
  const idx = s.indexOf(oldStr);
  if (idx < 0) { console.error('ANCHOR NOT FOUND:', oldStr.slice(0, 60)); process.exit(1); }
  s = s.replace(oldStr, newStr);
}
fs.writeFileSync(p, s);
console.log('stamp restored, size:', s.length);
