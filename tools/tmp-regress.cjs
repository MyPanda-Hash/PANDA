// 重构后回归验证：审批角标 + 审批流程 + 报表
const fs = require('fs');
const puppeteer = require(process.env.TEMP + '/pptr/node_modules/puppeteer-core');
const BASE = 'http://localhost:5173';
const OUT = 'F:/INCER/light-mes/docs/ref/mes-live/';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

(async () => {
  const browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: 'new', args: ['--no-first-run', '--no-default-browser-check', '--window-size=1680,1000'], userDataDir: process.env.TEMP + '/pptr/edge-regress', protocolTimeout: 120000 });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + String(e).slice(0, 200)));
  await page.goto(BASE + '/#/login', { waitUntil: 'networkidle2', timeout: 40000 });
  await new Promise((r) => setTimeout(r, 1200));
  if (page.url().includes('/login')) {
    await page.type('input[placeholder="用户名"]', 'admin');
    await page.type('input[placeholder="密码"]', '123456');
    await page.evaluate(() => { const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes('登')); if (b) b.click(); });
    await new Promise((r) => setTimeout(r, 2500));
  }
  const results = [];

  // 1) SALE_OUT：已审批种子单 → 角标
  await page.goto(BASE + '/#/panelx/list/SALE_OUT', { waitUntil: 'networkidle2', timeout: 40000 });
  await new Promise((r) => setTimeout(r, 2500));
  const d1 = await page.evaluate(() => ({
    stamp: (document.querySelector('.approved-stamp') || {}).textContent || '',
    docChip: (document.querySelector('.doc-chip') || {}).textContent || '',
    status: (document.querySelector('.doc-status') || {}).textContent || '',
    groups: [...document.querySelectorAll('.tb-group .act-name')].map((x) => x.textContent.trim()).join('|'),
    rows: document.querySelectorAll('.el-table__row').length,
  }));
  results.push({ step: 'SALE_OUT 角标', ...d1 });
  log('SALE_OUT:', JSON.stringify(d1));
  await page.screenshot({ path: OUT + 'refactor-check-01.png' });

  // 2) 报表（MANU_ORDER_DETAIL）
  await page.goto(BASE + '/#/panelx/list/MANU_ORDER_DETAIL', { waitUntil: 'networkidle2', timeout: 40000 });
  await new Promise((r) => setTimeout(r, 2000));
  const d2 = await page.evaluate(() => ({
    panel: (document.querySelector('.panel-info') || {}).textContent || '',
    rows: document.querySelectorAll('.el-table__row').length,
    head: (document.body.innerText || '').slice(0, 100).replace(/\n+/g, '|'),
  }));
  results.push({ step: '生产加工单明细表', ...d2 });
  log('报表:', JSON.stringify(d2));

  // 3) PROCESS_REPORT 审批流程（第一张 MR-0002 草稿 → 提交审批 → 审批通过）
  await page.goto(BASE + '/#/panelx/list/PROCESS_REPORT', { waitUntil: 'networkidle2', timeout: 40000 });
  await new Promise((r) => setTimeout(r, 2500));
  const d3a = await page.evaluate(() => ({
    docChip: (document.querySelector('.doc-chip') || {}).textContent || '',
    status: (document.querySelector('.doc-status') || {}).textContent || '',
    stamp: (document.querySelector('.approved-stamp') || {}).textContent || '',
    groups: [...document.querySelectorAll('.tb-group')].map((g) => (g.querySelector('.act-name') || {}).textContent).join('|'),
  }));
  results.push({ step: 'MR 初始', ...d3a });
  log('MR 初始:', JSON.stringify(d3a));
  // 提交审批（审批组主按钮 = 提交审批）
  const sub = await page.evaluate(() => {
    const grp = [...document.querySelectorAll('.tb-group')].find((g) => (g.querySelector('.act-name') || {}).textContent === '审批');
    if (!grp) return 'no-group';
    const main = grp.querySelector('.tb-main');
    if (!main) return 'no-main';
    main.click();
    return 'clicked';
  });
  await new Promise((r) => setTimeout(r, 1500));
  const d3b = await page.evaluate(() => ({
    status: (document.querySelector('.doc-status') || {}).textContent || '',
    stamp: (document.querySelector('.approved-stamp') || {}).textContent || '',
  }));
  results.push({ step: '提交审批', click: sub, ...d3b });
  log('提交审批:', JSON.stringify({ click: sub, ...d3b }));
  // 审批通过（下拉菜单）
  const pass = await page.evaluate(() => {
    const grp = [...document.querySelectorAll('.tb-group')].find((g) => (g.querySelector('.act-name') || {}).textContent === '审批');
    if (!grp) return 'no-group';
    const caret = grp.querySelector('.tb-caret');
    if (caret) caret.click();
    return 'menu-open';
  });
  await new Promise((r) => setTimeout(r, 700));
  const pass2 = await page.evaluate(() => {
    const items = [...document.querySelectorAll('.ctx-item')];
    const b = items.find((x) => x.textContent.trim() === '审批通过');
    if (!b) return 'no-item';
    b.click();
    return 'clicked';
  });
  await new Promise((r) => setTimeout(r, 1500));
  const d3c = await page.evaluate(() => ({
    status: (document.querySelector('.doc-status') || {}).textContent || '',
    stamp: (document.querySelector('.approved-stamp') || {}).textContent || '',
    approvedRows: document.querySelectorAll('.row-approved').length,
  }));
  results.push({ step: '审批通过', menu: pass, item: pass2, ...d3c });
  log('审批通过:', JSON.stringify({ menu: pass, item: pass2, ...d3c }));
  await page.screenshot({ path: OUT + 'refactor-check-02.png' });

  fs.writeFileSync(OUT + 'refactor-check.json', JSON.stringify(results, null, 1));
  log('errors:', errors.length ? errors.join(' ;; ') : 'none');
  await page.close();
  await browser.disconnect();
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });

