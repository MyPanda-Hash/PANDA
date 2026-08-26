import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const documentCenter = path.join(repoRoot, 'docs', 'README.md')
const roots = [
  'README.md',
  '工作记录.md',
  'docs',
  'frontend/src/core/README.md',
  'frontend/src/core/sdk/README.md',
  'tools/README.md',
  'tools/panels',
  'tools/tplus-grab/README.md',
]

const excluded = /[\\/](?:ref)(?:[\\/]|$)/
const errors = []

function collect(entry, files = []) {
  if (!fs.existsSync(entry) || excluded.test(entry)) return files
  const stat = fs.statSync(entry)
  if (stat.isDirectory()) {
    for (const child of fs.readdirSync(entry)) collect(path.join(entry, child), files)
  } else if (entry.toLowerCase().endsWith('.md')) {
    files.push(entry)
  }
  return files
}

function relative(file) {
  return path.relative(repoRoot, file).replaceAll('\\', '/')
}

function markdownHeadings(lines) {
  const headings = []
  let fence = null
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const fenceMatch = line.match(/^\s*(```|~~~)/)
    if (fenceMatch) {
      fence = fence ? null : fenceMatch[1]
      continue
    }
    if (fence) continue
    const match = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/)
    if (match) headings.push({ line: index + 1, level: match[1].length, text: match[2] })
  }
  return headings
}

function proseContent(content) {
  const output = []
  let fence = null
  for (const line of content.split(/\r?\n/)) {
    const fenceMatch = line.match(/^\s*(```|~~~)/)
    if (fenceMatch) {
      fence = fence ? null : fenceMatch[1]
      continue
    }
    if (!fence) output.push(line.replace(/`[^`]*`/g, ''))
  }
  return output.join('\n')
}

function auditStructure(file, content) {
  const name = relative(file)
  const prose = proseContent(content)
  if (content.charCodeAt(0) === 0xFEFF) errors.push(`${name}: contains UTF-8 BOM`)
  if (content.includes('\uFFFD')) errors.push(`${name}: contains Unicode replacement character`)
  if (!content.endsWith('\n')) errors.push(`${name}: missing final newline`)
  if (/<font\b|font-family\s*:|style\s*=/i.test(prose)) errors.push(`${name}: contains inline font/style markup`)
  const head = content.split(/\r?\n/).slice(0, 14).join('\n')
  for (const field of ['文档类型', '适用场景', '维护状态', '最后整理', '文档导航']) {
    if (!head.includes(`| ${field} |`)) errors.push(`${name}: missing metadata field ${field}`)
  }

  const headings = markdownHeadings(content.split(/\r?\n/))
  const h1 = headings.filter((heading) => heading.level === 1)
  if (h1.length !== 1) errors.push(`${name}: expected one H1, found ${h1.length}`)
  let previous = 0
  for (const heading of headings) {
    if (previous && heading.level > previous + 1) {
      errors.push(`${name}:${heading.line}: heading jumps H${previous} -> H${heading.level}`)
    }
    previous = heading.level
  }
}

function auditLinks(file, content) {
  const linkPattern = /!?\[[^\]]*\]\(([^)]+)\)/g
  for (const match of content.matchAll(linkPattern)) {
    const raw = match[1].trim().replace(/^<|>$/g, '')
    const target = raw.split('#')[0]
    if (!target || /^(?:https?:|mailto:|data:)/i.test(target)) continue
    const decoded = decodeURIComponent(target)
    const resolved = path.resolve(path.dirname(file), decoded)
    if (!fs.existsSync(resolved)) errors.push(`${relative(file)}: broken link ${raw}`)
  }
}

const files = [...new Set(roots.flatMap((entry) => collect(path.join(repoRoot, entry))))].sort()
const centerContent = fs.readFileSync(documentCenter, 'utf8')
const classified = new Set([documentCenter])
for (const match of centerContent.matchAll(/\[[^\]]*\]\(([^)]+\.md)(?:#[^)]+)?\)/g)) {
  classified.add(path.resolve(path.dirname(documentCenter), decodeURIComponent(match[1])))
}

for (const file of files) {
  const content = fs.readFileSync(file, 'utf8')
  auditStructure(file, content)
  auditLinks(file, content)
  if (!classified.has(file)) {
    errors.push(`${relative(file)}: not classified in docs/README.md`)
  }
}

if (errors.length) {
  console.error(`Documentation audit failed (${errors.length}):`)
  for (const error of errors) console.error(`- ${error}`)
  process.exit(1)
}

console.log(`Documentation audit passed: ${files.length} Markdown files`)
