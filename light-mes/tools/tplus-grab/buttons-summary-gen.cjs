#!/usr/bin/env node
/**
 * buttons-summary-gen.cjs —— 由 buttons.json/implementation.json 生成人读版按钮功能汇总
 * 用法：node tools/tplus-grab/buttons-summary-gen.cjs [--dir <批次目录>]
 */
const fs = require('node:fs')
const path = require('node:path')

const args = { dir: path.resolve('docs/ref/tplus-live/mech-20260901/isc') }
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i] === '--dir') args.dir = process.argv[++i]
}

// 只取主工具栏真顶层按钮：id 形如 toolbarContainer-<c>-<Cmd>（两段）；下拉项：...-<Cmd>-grp-ul-<Item>（osm/sd 为系统重复入口）
function parseToolbar(items) {
  const byId = new Map(items.map((i) => [i.commandId, i]))
  const tops = []
  for (const i of items) {
    const m = i.commandId.match(/^toolbarContainer-[A-Za-z0-9]+-([A-Za-z0-9]+)$/)
    if (m && i.text && i.text !== '|') tops.push(i)
  }
  const result = []
  for (const top of tops) {
    const cmd = top.commandId.match(/^toolbarContainer-[A-Za-z0-9]+-([A-Za-z0-9]+)$/)[1]
    const kids = []
    for (const i of items) {
      const m = i.commandId.match(new RegExp('^toolbarContainer-[A-Za-z0-9]+-' + cmd + '-grp-ul-([A-Za-z0-9]+)$'))
      if (m && i.text && !['设置默认功能', '|'].includes(i.text) && !/^(osd|sd|osm)/.test(m[1])) kids.push(i.text)
    }
    result.push({ text: top.text, shortcut: top.shortcut, shortcutText: top.shortcutText, children: [...new Set(kids)] })
  }
  return result
}

function fmtState(tls, title) {
  const lines = []
  const topLine = tls.filter((t) => !t.children.length).map((t) => t.text).join('、')
  lines.push('- ' + title + '顶层按钮：' + (topLine || '（无）'))
  const keys = tls.filter((t) => t.shortcut).map((t) => t.text + '(' + (t.shortcutText || 'Alt+' + t.shortcut) + ')')
  if (keys.length) lines.push('- ' + title + '快捷键：' + keys.join('、'))
  for (const t of tls.filter((t) => t.children.length)) lines.push('- ' + title + '下拉「' + t.text + '」：' + t.children.join('、'))
  return lines
}

function main() {
  const dir = args.dir
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.buttons.json')).sort()
  const out = []
  out.push('# 智能供应链单据按钮功能汇总（2026-09-01）')
  out.push('')
  out.push('- 来源：本目录各 `*.buttons.json`（列表态）与 `*.implementation.json`（新增/卡片态）。`commandId` 即工具栏元素 id（如 `toolbarContainer-gp-Add`），可直接用于本地按钮编码对照；快捷键取自 `accesskey` 与标题括号文本；可用状态为抓取时快照。')
  out.push('- 只读边界：仅枚举工具栏 DOM/JS 模型，并点击一次顶层「新增」进入空白卡片态；未执行保存、删除、审核、生单等任何写操作。')
  out.push('- 报表/工具/档案类页面（非 Voucher.aspx）的可见按钮保留在各自 `*.dom.json` 的 `page.buttons` 字段，同 QM 批次惯例。')
  out.push('- 采购核算单 ST2002、委外核算单 ST2010、序列号预生成/拆分/补录 ST1078/1079/1077 无顶层「新增」入口（核算单由系统生成、序列号工具为独立功能页），仅采集列表态。')
  out.push('- 生成方式：`node tools/tplus-grab/buttons-summary-gen.cjs`。')
  out.push('')
  for (const f of files) {
    const b = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'))
    let add = null
    try { add = JSON.parse(fs.readFileSync(path.join(dir, f.replace('.buttons.json', '.implementation.json')), 'utf8')) } catch {}
    out.push('## ' + b.code + ' ' + b.label)
    out.push('')
    out.push(...fmtState(parseToolbar(b.toolbarItems || []), '列表态'))
    if (add) out.push(...fmtState(parseToolbar(add.toolbarItems || []), '新增（卡片）态'))
    else out.push('- 新增态：页面无顶层「新增」，未采集')
    out.push('')
  }
  const target = path.join(dir, '智能供应链-按钮功能汇总.md')
  fs.writeFileSync(target, out.join('\n'))
  console.log('[done] ' + files.length + ' 张单据 -> ' + target + ' (' + Math.round(out.join('\n').length / 1024) + 'KB)')
}

main()
