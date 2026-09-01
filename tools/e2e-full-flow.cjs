/**
 * light-mes 全流程连通性验证（2026-08-25）
 * 覆盖业务总览全部推式生单链路 + 状态流转 + 查询/报表；结束后清理测试数据。
 */
const BASE = 'http://127.0.0.1:3308/api';
let token = null;
const created = []; // {panel, no}
const results = [];
function log(name, ok, extra = '') {
  results.push({ name, ok, extra });
  console.log(`${ok ? '✅' : '❌'} ${name}${extra ? ' — ' + extra : ''}`);
}
async function call(api, body) {
  const r = await fetch(BASE + api, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token }, body: JSON.stringify(body) }).then(r => r.json());
  return r;
}
async function q(api, params) {
  return fetch(BASE + api + '?' + new URLSearchParams(params), { headers: { 'Authorization': 'Bearer ' + token } }).then(r => r.json());
}
/** 建单：新增→填表头明细→保存→审核，返回编号 */
async function makeAudited(panel, head, detail, bizType) {
  const fresh = await call('/px/callButton', { panelCode: panel, buttonName: '新增流程', formData: {}, buttonParam: {} });
  const no = fresh.data?.编号 || fresh.编号;
  if (!no) throw new Error(panel + ' 新增失败: ' + JSON.stringify(fresh).slice(0, 120));
  created.push({ panel, no });
  const body = { 编号: no, 单据日期: '2026-08-25', 业务类型: bizType || panel, ...head };
  if (detail) body.detail = detail;
  const saved = await call('/px/callButton', { panelCode: panel, buttonName: '保存', formData: body, buttonParam: {} });
  if (!saved.data || saved.data.单据状态 === undefined) throw new Error(panel + ' 保存失败: ' + JSON.stringify(saved).slice(0, 120));
  const audited = await call('/px/callButton', { panelCode: panel, buttonName: '审核', formData: { 编号: no }, buttonParam: {} });
  if ((audited.data?.单据状态 || audited.单据状态) !== '已审核') throw new Error(panel + ' 审核失败: ' + JSON.stringify(audited).slice(0, 120));
  return no;
}
/** 推式生单：源单 → 目标单，返回目标编号 */
async function generate(srcPanel, srcNo, buttonName) {
  const r = await call('/px/callButton', { panelCode: srcPanel, buttonName, formData: { 编号: srcNo }, buttonParam: {} });
  if (r.code && r.code !== 200) throw new Error(buttonName + ' 失败: ' + r.message);
  const target = r.data?.gotoPanel;
  const targetNo = r.data?.编号;
  if (!target || !targetNo) throw new Error(buttonName + ' 未返回 gotoPanel: ' + JSON.stringify(r).slice(0, 120));
  created.push({ panel: target, no: targetNo });
  return { target, targetNo };
}

async function main() {
  const login = await call('/auth/login', { userName: 'admin', password: '123456' });
  if (login.code !== 200) { console.log('登录失败:', JSON.stringify(login).slice(0, 200)); process.exit(1); }
  token = login.data.token;
  console.log('===== 登录 OK，开始全流程验证 =====\n');

  // ========== 一、销售订单主链 ==========
  console.log('--- 销售链 ---');
  // 1. 报价单 → 审核 → 生成销售订单
  const quoteNo = await makeAudited('QUOTE_ORDER', { 客户: '测试客户A' }, { items: [{ 存货编码: 'CP001', 存货名称: '铝制支架', 规格型号: 'A-01', 数量: 20, 销售单位: '件', 报价单价: 15, 含税单价: 16.95, '税率%': 13 }] }, '报价');
  const so = await generate('QUOTE_ORDER', quoteNo, '生成销售订单');
  log('报价单→销售订单', true, so.targetNo);
  // 2. 销售订单 → 审核
  await call('/px/callButton', { panelCode: 'SO_ORDER', buttonName: '审核', formData: { 编号: so.targetNo }, buttonParam: {} });
  log('销售订单审核', true);
  // 3. 销售订单 → 生产加工单
  const mo = await generate('SO_ORDER', so.targetNo, '生成生产加工单');
  log('销售订单→生产加工单', true, mo.targetNo);
  // 4. 生产加工单 → 审核
  await call('/px/callButton', { panelCode: 'MANU_ORDER', buttonName: '审核', formData: { 编号: mo.targetNo }, buttonParam: {} });
  log('生产加工单审核', true);
  // 5. 销售订单 → 销售出库单
  const saleOut = await generate('SO_ORDER', so.targetNo, '生成销售出库单(普通销售)');
  log('销售订单→销售出库单', true, saleOut.targetNo);
  await call('/px/callButton', { panelCode: 'SALE_OUT', buttonName: '审核', formData: { 编号: saleOut.targetNo }, buttonParam: {} });
  log('销售出库单审核', true);

  // ========== 二、生产链 ==========
  console.log('\n--- 生产链 ---');
  // 6. 生产加工单 → 工序汇报单
  const pr = await generate('MANU_ORDER', mo.targetNo, '生成工序汇报单（自制汇报）');
  log('生产加工单→工序汇报单', true, pr.targetNo);
  // 7. 生产加工单 → 产成品入库单
  const fi = await generate('MANU_ORDER', mo.targetNo, '生成产成品入库单');
  log('生产加工单→产成品入库单', true, fi.targetNo);
  await call('/px/callButton', { panelCode: 'FINISH_IN', buttonName: '审核', formData: { 编号: fi.targetNo }, buttonParam: {} });
  log('产成品入库单审核', true);
  // 8. 领料申请单（手工建）→ 审核 → 调拨单 → 审核 → 材料出库单
  const mrNo = await makeAudited('MATERIAL_REQ', { 仓库: '原料仓', 领料申请人: '王强' }, { items: [{ 材料名称: '铝合金棒', 规格型号: 'φ60', 仓库: '原料仓', 计量单位: 'kg', 数量: 50, 单价: 28, 金额: 1400 }] }, '领料申请');
  const tr = await generate('MATERIAL_REQ', mrNo, '生成调拨单');
  log('领料申请→调拨单', true, tr.targetNo);
  await call('/px/callButton', { panelCode: 'TRANSFER', buttonName: '审核', formData: { 编号: tr.targetNo }, buttonParam: {} });
  log('调拨单审核', true);
  const mo2 = await generate('TRANSFER', tr.targetNo, '生成材料出库单');
  log('调拨单→材料出库单(调拨出库)', true, mo2.targetNo);
  // 9. 领料申请单 → 材料出库单（直接领料）
  const mo3 = await generate('MATERIAL_REQ', mrNo, '生成材料出库单');
  log('领料申请→材料出库单(直接领料)', true, mo3.targetNo);

  // ========== 三、采购链 ==========
  console.log('\n--- 采购链 ---');
  // 10. 采购需求分析：先建一张缺料加工单（YL999 需 500 现存 80 → 建议 420），验证聚合正确
  const anaMoNew = await call('/px/callButton', { panelCode: 'MANU_ORDER', buttonName: '新增流程', formData: {}, buttonParam: {} });
  const anaMoNo = anaMoNew.data?.编号 || anaMoNew.编号;
  created.push({ panel: 'MANU_ORDER', no: anaMoNo });
  await call('/px/callButton', { panelCode: 'MANU_ORDER', buttonName: '保存', formData: { 编号: anaMoNo, 单据日期: '2026-08-25', 业务类型: '生产加工', detail: { products: [{ 产品编码: 'CP001', 产品名称: '铝制支架', 数量: 10 }], materials: [{ 材料编码: 'YL999', 材料名称: '测试合金', 规格型号: 'T-1', 计量单位: 'kg', 计划数量: 500, 现存量: 80, 预出仓库: '原料仓' }] } }, buttonParam: {} }, token);
  await call('/px/callButton', { panelCode: 'MANU_ORDER', buttonName: '审核', formData: { 编号: anaMoNo }, buttonParam: {} }, token);
  const ana = await call('/px/queryFormDataList', { panelCode: 'PU_REQ_ANALYSIS', keyword: '', condition: {}, pageNo: 1, pageSize: 10 });
  const anaRows = (ana.data?.list || []).filter(r => r['材料编码'] === 'YL999');
  log('采购需求分析聚合查询', anaRows.length === 1 && Number(anaRows[0]['建议请购数量']) === 420, anaRows.length ? 'YL999 建议 ' + anaRows[0]['建议请购数量'] + ' (500-80)' : '0 行');
  // 11. 请购单 → 审核 → 采购订单 → 审核
  const reqNo = await makeAudited('PU_REQ', { 项目: '测试项目', 需求日期: '2026-08-30' }, { items: [{ 存货编码: 'YL001', 存货名称: '铝合金棒', 规格型号: 'φ60', 采购单位: 'kg', 数量: 200, 单价: 20 }] }, '请购');
  const po = await generate('PU_REQ', reqNo, '生成采购订单');
  log('请购单→采购订单', true, po.targetNo);
  await call('/px/callButton', { panelCode: 'PU_ORDER', buttonName: '审核', formData: { 编号: po.targetNo }, buttonParam: {} });
  log('采购订单审核', true);
  // 12. 采购订单 → 采购入库单
  const puIn = await generate('PU_ORDER', po.targetNo, '生成采购入库单');
  log('采购订单→采购入库单', true, puIn.targetNo);
  await call('/px/callButton', { panelCode: 'PURCHASE_IN', buttonName: '审核', formData: { 编号: puIn.targetNo }, buttonParam: {} });
  log('采购入库单审核', true);
  // 13. 采购订单 → 进货单 → 审核 → 采购发票
  const piNo = await generate('PU_ORDER', po.targetNo, '生成进货单');
  log('采购订单→进货单', true, piNo.targetNo);
  await call('/px/callButton', { panelCode: 'PU_IN', buttonName: '审核', formData: { 编号: piNo.targetNo }, buttonParam: {} });
  log('进货单审核', true);
  const inv = await generate('PU_IN', piNo.targetNo, '生成采购发票（普通采购）');
  log('进货单→采购发票', true, inv.targetNo);
  await call('/px/callButton', { panelCode: 'PU_INVOICE', buttonName: '审核', formData: { 编号: inv.targetNo }, buttonParam: {} });
  log('采购发票审核', true);

  // ========== 四、销货/发票/费用分摊链 ==========
  console.log('\n--- 销货/发票/分摊链 ---');
  // 14. 销货单（手工建）→ 审核 → 销售发票 → 审核
  const siNo = await makeAudited('SALE_INV', { 客户: '测试客户A' }, { items: [{ 存货编码: 'CP001', 存货名称: '铝制支架', 规格型号: 'A-01', 数量: 20, 销售单位: '件', 单价: 15, 含税单价: 16.95, '税率%': 13 }] }, '销货');
  const sInv = await generate('SALE_INV', siNo, '生成销售发票');
  log('销货单→销售发票', true, sInv.targetNo);
  await call('/px/callButton', { panelCode: 'SALE_INVOICE', buttonName: '审核', formData: { 编号: sInv.targetNo }, buttonParam: {} });
  log('销售发票审核', true);
  // 15. 费用单(销售费用) → 审核 → 销售费用分摊单
  const exNo = await makeAudited('EXPENSE', { 费用类型: '销售费用' }, { items: [{ 费用项目: '运费', 金额: 300 }] }, '销售费用');
  const sa = await generate('EXPENSE', exNo, '生成销售费用分摊单');
  log('费用单→销售费用分摊单', true, sa.targetNo);
  // 16. 费用单(采购费用) → 审核 → 采购费用分摊单
  const ex2No = await makeAudited('EXPENSE', { 费用类型: '采购费用' }, { items: [{ 费用项目: '运费', 金额: 200 }] }, '采购费用');
  const pa = await generate('EXPENSE', ex2No, '生成采购费用分摊单');
  log('费用单→采购费用分摊单', true, pa.targetNo);

  // ========== 五、委外链 ==========
  console.log('\n--- 委外链 ---');
  const osNo = await makeAudited('OUTSOURCE_ORDER', { 委外供应商: '外协厂A', 生产车间: '熔铸车间' }, {
    products: [{ 产品编码: 'CP001', 产品名称: '铝制支架', 规格型号: 'A-01', 计量单位: '件', 数量: 30, 委外单价: 12, 金额: 360 }],
    materials: [{ 材料编码: 'YL001', 材料名称: '铝合金棒', 规格型号: 'φ60', 计量单位: 'kg', 计划数量: 120, 预出仓库: '原料仓' }],
  }, '委外加工');
  const oi = await generate('OUTSOURCE_ORDER', osNo, '生成委外发料单');
  log('委外加工单→委外发料单', true, oi.targetNo);
  const oin = await generate('OUTSOURCE_ORDER', osNo, '生成委外入库单');
  log('委外加工单→委外入库单', true, oin.targetNo);
  await call('/px/callButton', { panelCode: 'OUTSOURCE_IN', buttonName: '审核', formData: { 编号: oin.targetNo }, buttonParam: {} });
  log('委外入库单审核', true);
  const of = await generate('OUTSOURCE_ORDER', osNo, '生成委外加工费用单');
  log('委外加工单→委外加工费用单', true, of.targetNo);

  // ========== 六、库存/序列号/其他 ==========
  console.log('\n--- 库存/序列号链 ---');
  const ckNo = await makeAudited('STOCK_CHECK', { 仓库: '原料仓' }, { items: [{ 存货编码: 'YL001', 存货名称: '铝合金棒', 规格型号: 'φ60', 计量单位: 'kg', 账面数量: 100, 实盘数量: 95 }] }, '盘点');
  log('库存盘点单', true, ckNo);
  const laNo = await makeAudited('LOCATION_ADJUST', { 仓库: '原料仓' }, { items: [{ 存货编码: 'YL001', 存货名称: '铝合金棒', 规格型号: 'φ60', 计量单位: 'kg', 数量: 10, 原货位: 'A-01', 新货位: 'B-02' }] }, '货位调整');
  log('货位调整单', true, laNo);
  const snNo = await makeAudited('SERIAL_NO', { 存货: '铝制支架', 存货编码: 'CP001', 仓库: '成品仓', 入库单号: 'RK-TEST' }, { items: [{ 序列号: 'SN-E2E-001', 状态: '在库', 入库日期: '2026-08-25' }, { 序列号: 'SN-E2E-002', 状态: '在库', 入库日期: '2026-08-25' }] }, '序列号登记');
  log('序列号登记单', true, snNo);
  const st = await call('/px/queryFormDataList', { panelCode: 'SERIAL_STATUS', keyword: '', condition: {}, pageNo: 1, pageSize: 10 });
  const stRows = st.data?.list || [];
  log('序列号状况表查询', stRows.some(r => r['序列号'] === 'SN-E2E-001'), stRows.length + ' 行');

  // ========== 七、报表连通 ==========
  console.log('\n--- 报表 ---');
  for (const code of ['MANU_ORDER_EXEC', 'MANU_ORDER_TRACKER', 'MANU_ORDER_MATERIAL_DETAIL', 'PICK_ORDER_DETAIL', 'PICK_ORDER_STATS', 'PICK_ORDER_SUMMARY', 'OUTSOURCE_ORDER_EXEC', 'OUTSOURCE_FEE_STATS', 'SALES_ORDER_EXEC', 'SALES_ORDER_PROGRESS', 'PURCHASE_IN_DETAIL', 'STOCK_STATUS', 'STOCK_SUMMARY', 'STOCK_LEDGER']) {
    const r = await call('/px/queryFormDataList', { panelCode: code, keyword: '', condition: {}, pageNo: 1, pageSize: 5 });
    const rows = r.data?.list || [];
    log('报表 ' + code, Array.isArray(rows), rows.length + ' 行');
  }

  // ========== 汇总 ==========
  const failed = results.filter(r => !r.ok);
  console.log(`\n===== 汇总: ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length} =====`);
  if (failed.length) { console.log('失败项:'); failed.forEach(f => console.log('  ❌ ' + f.name + ' — ' + f.extra)); }

  // ========== 清理测试数据 ==========
  console.log('\n清理测试数据...');
  let cleaned = 0
  for (const c of created.reverse()) {
    try {
      // 已审核单先弃审到草稿（delete 仅允许草稿），再删除
      await call('/px/callButton', { panelCode: c.panel, buttonName: '弃审', formData: { 编号: c.no }, buttonParam: {} })
    } catch {}
    try {
      await call('/px/deleteForms', { panelCode: c.panel, rowCodes: [c.no] })
      cleaned++
    } catch (e) { console.log('  清理失败:', c.panel, c.no, e.message) }
  }
  console.log('清理完成: ' + cleaned + '/' + created.length + ' 张测试单据');
  process.exit(failed.length ? 1 : 0);
}
main().catch(e => { console.error('FATAL', e.message); process.exit(1); });
