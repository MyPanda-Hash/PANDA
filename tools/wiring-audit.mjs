import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

/**
 * 接线门禁：找出「有测试保护、但生产代码从不调用」的方法。
 *
 * 为什么需要它：2026-11-16 实测到一个真实财务缺陷 ——
 * AccountingService 的金额口径函数 accountingAmount() 有实现、有 3 条单测、注释也写明了
 * 「销售出库按成本结转」，但 PxService.postAccounting 一直用通用 settlementAmount() 调它。
 * 结果：销售出库成本凭证按销售额记账（600 元）而不是成本金额（45.77 元），
 * 主营业务成本虚增 13 倍，库存与利润同时失真。
 *
 * 关键在于：这类缺陷单测全绿、门禁全绿，因为测试测的是那个没人调用的函数本身。
 *
 * 用法：node tools/wiring-audit.mjs [--list]
 * 退出码 1 = 发现未登记的「测试独有」方法。
 */

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backendSrc = path.join(root, 'backend', 'src')
const mainDir = path.join(backendSrc, 'main', 'java')
const testDir = path.join(backendSrc, 'test', 'java')

const showList = process.argv.includes('--list')

/** 已知且有意保留的未接线成员：登记名字与原因，避免门禁变成噪声。 */
const ALLOWED = new Map([
  ['tracePanels', '注释已声明「供测试与文档核对」，是对外核对口径的查询入口'],
  ['prefixes', '单号前缀表的只读快照，供测试与文档核对'],
  ['validateConfiguration', '@PostConstruct，由 Spring 容器调用'],
  ['doFilterInternal', 'OncePerRequestFilter 模板方法，由父类 doFilter 调用'],
  ['loadDocs', 'trace() 已内联同一读取逻辑，本方法是供测试构造 Docs 的等价封装'],
  ['summarize', '待处理：拣货工作台逐行输出，不做整单汇总'],
  ['complete', '待处理：随 summarize 一同未接线'],
  ['nextRevision', '待处理：修订记录由 PdmChangeActionHandler 另行构造'],
  ['markSuperseded', '待处理：随 nextRevision 一同未接线'],
])

/** 数据承载类文件不参与：getter/setter 由框架按名反射调用。 */
const SKIP_PATTERNS = ['/entity/', '/dto/', '/config/JwtUtil.java']

/** 框架按注解调度的入口，生产代码里当然搜不到调用点。 */
const FRAMEWORK_ANNOTATIONS = [
  '@PostConstruct', '@PreDestroy', '@Override', '@Bean', '@Scheduled',
  '@EventListener', '@Async', '@Aspect', '@Around', '@Before', '@After',
  '@GetMapping', '@PostMapping', '@PutMapping', '@DeleteMapping', '@RequestMapping',
  '@ExceptionHandler', '@KafkaListener', '@RabbitListener',
]

const BOILERPLATE = /^(?:get|set|is|has|can)[A-Z]|^(?:toString|hashCode|equals|canEqual)$/

/**
 * 去掉注释与字符串字面量，只留真正的代码。
 *
 * 必须这么做：注释里提到方法名（例如「用 accountingAmount 而不是 settlementAmount」）
 * 会被朴素计数当成一次调用，门禁对真实缺陷直接漏报 —— 反向验证时实测到过这一点。
 * 用字符扫描而不是正则，避免转义把规则写坏。
 */
function stripNonCode(text) {
  const out = []
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    const next = text[i + 1]
    if (ch === '/' && next === '/') {
      while (i < text.length && text[i] !== '\n') i += 1
      continue
    }
    if (ch === '/' && next === '*') {
      i += 2
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i += 1
      i += 2
      continue
    }
    if (ch === '"' || ch === "'") {
      const quote = ch
      i += 1
      while (i < text.length && text[i] !== quote) {
        if (text[i] === String.fromCharCode(92)) i += 1
        i += 1
      }
      i += 1
      out.push(quote + quote)
      continue
    }
    out.push(ch)
    i += 1
  }
  return out.join('')
}

function javaFiles(base, output = []) {
  if (!fs.existsSync(base)) return output
  for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
    const target = path.join(base, entry.name)
    if (entry.isDirectory()) javaFiles(target, output)
    else if (entry.name.endsWith('.java')) output.push(target)
  }
  return output
}

const mainFiles = new Map()
for (const file of javaFiles(mainDir)) mainFiles.set(file, fs.readFileSync(file, 'utf8'))
const testFiles = new Map()
for (const file of javaFiles(testDir)) testFiles.set(file, fs.readFileSync(file, 'utf8'))

const mainCode = new Map()
for (const [file, text] of mainFiles) mainCode.set(file, stripNonCode(text))
const testCode = new Map()
for (const [file, text] of testFiles) testCode.set(file, stripNonCode(text))

function countIn(files, name) {
  const pattern = new RegExp('\\b' + name + '\\b', 'g')
  let total = 0
  for (const text of files.values()) total += (text.match(pattern) || []).length
  return total
}

const decl =
  /^[ \t]{4,}((?:(?:public|protected|static|final|synchronized|abstract|default|native)\s+)*)(?:<[^>]+>\s*)?([A-Za-z_][\w<>[\].]*?(?:\s*<[^>]*>)?)\s+([a-zA-Z_]\w*)\s*\(/

const findings = []
const seen = new Set()

for (const [file, text] of mainFiles) {
  const relative = path.relative(root, file)
  const normalized = file.split(path.sep).join('/')
  if (SKIP_PATTERNS.some((pattern) => normalized.includes(pattern))) continue
  const className = path.basename(file, '.java')
  const lines = text.split('\n')
  for (let i = 0; i < lines.length; i += 1) {
    const match = decl.exec(lines[i])
    if (!match) continue
    const modifiers = match[1]
    const name = match[3]
    if (name === className) continue
    if (modifiers.includes('private')) continue
    if (modifiers.includes('abstract')) continue
    if (BOILERPLATE.test(name)) continue
    // 接口方法只有签名，没有实现体
    if (!lines[i].includes('{')) continue

    const key = relative + '#' + name
    if (seen.has(key)) continue
    seen.add(key)

    const previous = lines.slice(Math.max(0, i - 3), i).join('\n')
    if (FRAMEWORK_ANNOTATIONS.some((annotation) => previous.includes(annotation))) continue

    // 声明本身计 1 次；生产代码里再没有别的引用 = 未接线
    if (countIn(mainCode, name) - 1 > 0) continue
    const testRefs = countIn(testCode, name)
    if (testRefs === 0) continue

    findings.push({ name, relative, line: i + 1, testRefs })
  }
}

const unknown = findings.filter((item) => !ALLOWED.has(item.name))
const allowed = findings.filter((item) => ALLOWED.has(item.name))

if (showList || unknown.length) {
  console.log('测试独有方法（生产代码零调用）：')
  for (const item of [...unknown, ...allowed].sort((a, b) => b.testRefs - a.testRefs)) {
    const mark = ALLOWED.has(item.name) ? '已登记' : '未登记'
    console.log('  [' + mark + '] ' + item.name.padEnd(26) + ' 测试引用 ' +
      String(item.testRefs).padStart(2) + '  ' + item.relative + ':' + item.line)
  }
  console.log('')
}

if (unknown.length) {
  console.log('接线门禁失败：' + unknown.length + ' 个未登记方法只被测试调用，生产代码从不调用。')
  console.log('这类方法会让「单测全绿」变成假安全感（accountingAmount 就是这么漏掉一个财务缺陷的）。')
  console.log('处理：① 真正接上生产调用；② 删掉方法与它的测试；③ 确认有意保留后加进 ALLOWED 并写明原因。')
  process.exit(1)
}

console.log('接线门禁通过：' + allowed.length + ' 个已登记成员有意未接线，无新增测试独有方法。')
