#!/usr/bin/env node
/**
 * tplus-gen：把 grab.cjs 产出的 dom.json 转成 1:1 骨架 HTML。
 * 可单独使用：node tools/tplus-grab/gen.cjs <dom.json> [out.html]
 */
const fs = require('node:fs')

function stripFont(t) { return String(t || '').replace(/<[^>]*>/g, '') }

function genHtml(dom) {
  // 第一组主网格的可见列（到第二个 ID 列为止）
  const cols = []
  let idCount = 0
  for (const c of dom.cols || []) {
    if (c.t === 'ID') { idCount++; if (idCount >= 2) break }
    if (!c.hidden && c.t) cols.push(c)
  }
  const th = cols.map((c) => {
    const raw = stripFont(c.t)
    const required = c.t.includes('<font>')
    return `<th style="width:${c.w}"><span style="color:#FF0033">${required ? '*' : ''}</span>${raw}</th>`
  }).join('')

  const rows = [
    ['自主生产', 'CP001', '', '演示产品A', 'GG-A', 'A', 'BOM-001', '单阶', '件', '200', '200', '0', '200', '', '200', ''],
    ['来料加工', 'CP002', '', '演示产品B', 'GG-B', 'B', 'BOM-002', '尾阶', '件', '150', '150', '0', '150', '', '150', ''],
    ['自主生产', 'CP003', '', '演示产品C', 'GG-C', 'C', 'BOM-003', '单阶', '套', '80', '80', '0', '80', '', '80', ''],
  ].map((r) => `<tr>${r.map((v, i) => (i === 1 ? `<td class="c">${v}</td>` : `<td>${v}</td>`)).join('')}</tr>`).join('')

  const toolbar = (dom.toolbar && dom.toolbar.length ? dom.toolbar : ['新增', '选单', '修改', '保存', '删除', '弃审', '审核情况', '生单', '变更', '工具', '联查', '设置', '打印', '更多'])
  const toolbarHtml = toolbar.map((t) => `<span class="g">${t}</span>`).join('<span class="sep">|</span>')

  const query = (dom.query || []).filter((q) => q.y >= 20 && q.y < 80).slice(0, 12)
  const queryHtml = query.map((q) => {
    const req = q.label.startsWith('*')
    const label = req ? q.label.slice(1) : q.label
    return `<span class="qf"><span class="ql">${req ? '<span style="color:#FF0033">*</span>' : ''}${label}</span><span class="qi"${q.inW ? ` style="width:${q.inW}px"` : ''}></span></span>`
  }).join('')

  const tabs = dom.tabs || []
  const tabsHtml = tabs.length
    ? tabs.map((t, i) => `<span class="tab${i === 0 ? ' on' : ''}">${t.text}</span>`).join('')
    : '<span class="tab on">明细</span>'

  const body = dom.body || {}
  const font = body.font ? body.font.split(',')[0].replace(/"/g, '') : 'Arial'
  const fsPx = body.fs || '12px'

  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 1600px; height: 1000px; overflow: hidden; background: #fff; font-family: ${font}, "Microsoft YaHei", "Pingfang SC", sans-serif; font-size: ${fsPx}; }
.toolbar { display: flex; align-items: center; height: 31px; padding: 0 20px; background: #fff; border-bottom: 1px solid #eee; }
.toolbar .g { font-size: 14px; color: #333; margin: 0 5px; }
.toolbar .sep { color: #ccc; margin: 0 6px; }
.qrow { display: flex; align-items: center; height: 30px; padding: 0 24px; gap: 14px; background: #fff; }
.qf { display: inline-flex; align-items: center; gap: 4px; }
.ql { color: #333; }
.qi { display: inline-block; width: 93px; height: 22px; background: #fff; }
.tabs { display: flex; align-items: center; height: 30px; padding: 0 24px; gap: 18px; background: #fff; }
.tabs .tab { font-size: 14px; font-weight: 700; color: #333; padding-bottom: 4px; }
.tabs .tab.on { color: #3788FF; border-bottom: 2px solid #3788FF; }
.gridwrap { padding: 0 24px; overflow: hidden; background: #fff; }
table { border-collapse: collapse; table-layout: fixed; width: 2160px; font-size: ${fsPx}; }
thead th { background: #E0E0E0; color: #333; font-weight: 600; height: 27px; border-right: 1px solid #d0d0d0; border-bottom: 1px solid #d0d0d0; white-space: nowrap; overflow: hidden; }
tbody td { height: 26px; border-right: 1px solid #f0f0f0; border-bottom: 1px solid #f0f0f0; text-align: center; white-space: nowrap; overflow: hidden; color: #333; }
tbody td.c { color: #3788FF; }
</style></head>
<body>
  <div class="toolbar">${toolbarHtml}</div>
  <div class="qrow">${queryHtml}</div>
  <div class="tabs">${tabsHtml}</div>
  <div class="gridwrap">
    <table>
      <thead><tr>${th}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
</body></html>`
}

function main() {
  const argv = process.argv.slice(2)
  if (!argv[0]) { console.error('用法: node gen.cjs <dom.json> [out.html]'); process.exit(1) }
  const dom = JSON.parse(fs.readFileSync(argv[0], 'utf8'))
  const out = argv[1] || argv[0].replace(/\.dom\.json$/, '.html')
  fs.writeFileSync(out, genHtml(dom))
  console.log('written: ' + out)
}

if (require.main === module) main()
module.exports = { genHtml }