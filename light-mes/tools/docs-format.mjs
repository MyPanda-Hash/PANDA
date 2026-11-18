import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const reviewedAt = '2026-08-27'

const documents = {
  'README.md': ['项目说明', '项目入门', '生效'],
  'docs/README.md': ['文档导航', '全部场景', '生效'],
  'docs/frontend/前端面板设计.md': ['前端设计', '前端面板设计', '生效'],
  'docs/backend/后端逻辑设计.md': ['后端设计', '后端逻辑设计', '生效'],
  'docs/deploy/服务器部署.md': ['部署手册', '服务器部署', '生效'],
  'docs/development/开发与质量.md': ['开发规范', '开发与质量', '生效'],
  'frontend/src/core/README.md': ['包说明', '前端面板设计', '生效'],
  'tools/README.md': ['工具说明', '开发与质量', '生效'],
  'tools/tplus-grab/README.md': ['工具说明', '开发与质量', '生效'],
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
