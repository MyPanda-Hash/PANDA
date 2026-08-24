import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const reviewedAt = '2026-08-24'

const documents = {
  'README.md': ['项目说明', '项目入门', '生效'],
  '工作记录.md': ['工作日志', '测试、问题与工作日志', '历史日志'],
  'docs/README.md': ['文档导航', '全部场景', '生效'],
  'docs/文档编写规范.md': ['开发规范', '全部场景', '生效'],
  'docs/开发规范与需求总览.md': ['开发规范', '通用架构与 SDK', '生效'],
  'docs/架构分层.md': ['开发规范', '通用架构与 SDK', '生效'],
  'docs/页面开发规范.md': ['开发规范', '面板开发', '生效'],
  'docs/BUG总结.md': ['问题记录', '测试、问题与工作日志', '持续维护'],
  'docs/工作记录-2026-08-17-产成品材料BOM联动.md': ['工作日志', '生产制造', '历史日志'],
  'docs/deploy/部署说明.md': ['部署手册', '部署与运维', '生效'],
  'docs/deploy/部署手册-实测全流程.md': ['部署记录', '部署与运维', '历史记录'],
  'docs/design/通用面板设计规范-v1.md': ['开发规范', '通用架构与 SDK', '生效'],
  'docs/design/面板交互设计规范.md': ['开发规范', '面板开发', '生效'],
  'docs/design/T+轻MES生产加工单-真实面板设计.md': ['场景设计', '生产制造', '调研基线'],
  'docs/design/T+工艺路线-真实面板设计.md': ['场景设计', '生产制造', '调研基线'],
  'docs/design/T+库存核算-真实面板设计.md': ['场景设计', '库存与供应链', '调研基线'],
  'docs/design/T+销售出库单-产成品入库单-工序汇报单-真实面板设计.md': ['场景设计', '库存与供应链', '调研基线'],
  'docs/design/T+审批面板调研-20260814.md': ['调研记录', '审批与流程', '调研基线'],
  'docs/design/T+报表类28面板-真实后端与前端设计.md': ['场景设计', '报表', '生效'],
  'frontend/src/core/README.md': ['包说明', '通用架构与 SDK', '生效'],
  'frontend/src/core/sdk/README.md': ['SDK 说明', '通用架构与 SDK', '生效'],
  'tools/tplus-grab/README.md': ['工具说明', '测试、问题与工作日志', '生效'],
  'tools/panels/PURCHASE_IN/PURCHASE_IN.design.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/PURCHASE_IN/PURCHASE_IN.select.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/PU_IN/PU_IN.notes.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/ARRIVAL_IN/ARRIVAL_IN.notes.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/SALE_OUT/SALE_OUT.design.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/SALE_OUT/SALE_OUT.select.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/SALE_INV/SALE_INV.notes.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/PICK_ORDER/PICK_ORDER.notes.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/MATERIAL_OUT/MATERIAL_OUT.design.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/MATERIAL_OUT/MATERIAL_OUT.select.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/MATERIAL_REQ/MATERIAL_REQ.notes.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/OTHER_IN/OTHER_IN.design.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/OTHER_IN/OTHER_IN.select.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/OTHER_OUT/OTHER_OUT.design.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/OTHER_OUT/OTHER_OUT.select.md': ['场景设计', '库存与供应链', '调研基线'],
  'tools/panels/FINISH_INSPECT/FINISH_INSPECT.notes.md': ['场景设计', '质量与车间', '调研基线'],
  'tools/panels/INSPECTION/INSPECTION.notes.md': ['场景设计', '质量与车间', '调研基线'],
  'tools/panels/DISPATCH/DISPATCH.notes.md': ['场景设计', '质量与车间', '调研基线'],
}

function navigationTarget(file) {
  if (file === 'docs/README.md') return '当前文档'
  const target = path.relative(path.dirname(file), 'docs/README.md').replaceAll('\\', '/')
  return `[文档中心](${target})`
}

function metadata(file, values) {
  return [
    '| 属性 | 内容 |',
    '|---|---|',
    `| 文档类型 | ${values[0]} |`,
    `| 适用场景 | ${values[1]} |`,
    `| 维护状态 | ${values[2]} |`,
    `| 最后整理 | ${reviewedAt} |`,
    `| 文档导航 | ${navigationTarget(file)} |`,
  ]
}

function removeMetadata(lines) {
  let index = 2
  while (index < lines.length && lines[index] === '') index += 1
  if (lines[index] !== '| 属性 | 内容 |') return lines
  let end = index
  while (end < lines.length && /^\|.*\|$/.test(lines[end])) end += 1
  while (end < lines.length && lines[end] === '') end += 1
  return [...lines.slice(0, 2), ...lines.slice(end)]
}

function normalizeBlankLines(lines) {
  const output = []
  let fence = null
  let blank = false
  for (const raw of lines) {
    const line = raw.replace(/[ \t]+$/g, '')
    const fenceMatch = line.match(/^\s*(```|~~~)/)
    if (fenceMatch) fence = fence ? null : fenceMatch[1]
    if (!fence && line === '') {
      if (!blank) output.push(line)
      blank = true
    } else {
      output.push(line)
      blank = false
    }
  }
  while (output.at(-1) === '') output.pop()
  return output
}

for (const [file, values] of Object.entries(documents)) {
  const absolute = path.join(repoRoot, file)
  if (!fs.existsSync(absolute)) {
    console.error(`Missing managed document: ${file}`)
    process.exitCode = 1
    continue
  }
  let content = fs.readFileSync(absolute, 'utf8').replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n')
  let lines = content.split('\n')
  if (!/^#\s+/.test(lines[0])) {
    console.error(`Missing H1: ${file}`)
    process.exitCode = 1
    continue
  }
  lines = removeMetadata(lines)
  lines = [lines[0], '', ...metadata(file, values), '', ...lines.slice(2)]
  lines = normalizeBlankLines(lines)
  fs.writeFileSync(absolute, `${lines.join('\n')}\n`, 'utf8')
}

if (!process.exitCode) console.log(`Formatted ${Object.keys(documents).length} Markdown files`)