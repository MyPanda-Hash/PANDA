/**
 * entry-audit.mjs — 自定义页面入口一致性校验
 *
 * <p>背景：工单排产改版时只改了独立页，漏掉列表里打开的那个弹窗，
 * 结果同一张加工单从两个入口进去是两套界面。规则写在
 * docs/frontend/面板入口清单.md，这个脚本负责让规则可执行。
 *
 * 校验三件事：
 * 1. router 里每个自定义页面都能在入口清单中登记；
 * 2. 入口清单里登记的页面都有对应路由；
 * 3. 源码里的站内跳转字面量都指向真实路由（防死链）。
 *
 * 用法：node tools/entry-audit.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const frontendRoot = path.join(repoRoot, 'frontend', 'src')
const routerFile = path.join(frontendRoot, 'router', 'index.js')
const inventoryFile = path.join(repoRoot, 'docs', 'frontend', '面板入口清单.md')

const errors = []

function relative(file) {
  return path.relative(repoRoot, file).replaceAll('\\', '/')
}

/** 由相对文件路径生成页面路径，router 里子路由写的是去掉前导 / 的形式 */
function absoluteRoute(childPath) {
  return childPath.startsWith('/') ? childPath : `/${childPath}`
}

/** 收集 router 中挂到 views/ 的自定义页面（跳过通用面板、登录和 404） */
function readCustomRoutes() {
  const source = fs.readFileSync(routerFile, 'utf8')
  const routes = new Map()
  const pattern = /\{\s*path:\s*'([^']+)'\s*,\s*component:\s*\(\)\s*=>\s*import\('@\/views\/([^']+)'\)/g
  for (const match of source.matchAll(pattern)) {
    const routePath = absoluteRoute(match[1])
    if (routePath === '/login' || routePath.includes(':')) continue
    routes.set(routePath, `views/${match[2]}`)
  }
  return routes
}

/** 入口清单第 2 节表格里的页面路径（第一列以 / 开头的行） */
function readInventoryPaths() {
  const content = fs.readFileSync(inventoryFile, 'utf8')
  const paths = new Set()
  for (const line of content.split(/\r?\n/)) {
    const cell = line.split('|')[1]
    if (!cell) continue
    // 单元格形如 "`/mes/plan` 工单排产"，只取开头的路径
    const match = cell.trim().replace(/^`/, '').match(/^(\/[A-Za-z0-9/_-]+)/)
    if (match) paths.add(match[1])
  }
  return paths
}

const routes = readCustomRoutes()
const inventory = readInventoryPaths()

// 1. 路由页面必须登记入口
for (const [routePath] of routes) {
  if (!inventory.has(routePath)) {
    errors.push(`${routePath}: 路由已存在但未在 docs/frontend/面板入口清单.md 登记入口`)
  }
}

// 2. 登记页面必须有路由
for (const inventoryPath of inventory) {
  if (!routes.has(inventoryPath)) {
    errors.push(`${inventoryPath}: 入口清单登记了该页面，但 router/index.js 里没有对应路由`)
  }
}

// 3. 站内跳转字面量必须能落到路由或通用面板
const moduleRoots = ['/mes/', '/scm/', '/prod/', '/top/', '/sys/', '/print/', '/panelx/', '/dashboard']
const panelPath = /^\/panelx\/(?:list|form)\/[A-Z0-9_]+$/
const known = new Set([...routes.keys(), '/login', '/dashboard'])

function collectSourceFiles(dir, files = []) {
  for (const child of fs.readdirSync(dir)) {
    const full = path.join(dir, child)
    const stat = fs.statSync(full)
    if (stat.isDirectory()) collectSourceFiles(full, files)
    else if (/\.(?:js|vue)$/.test(child)) files.push(full)
  }
  return files
}

/**
 * 只检查「跳转语句」里的路径字面量：路由 API 调用、页签、新窗口、路由对象和 path 变量。
 * 后端接口路径（request.get('/sys/dept/tree')）和拼接前缀（'/panelx/list/' + code）不算跳转，
 * 否则会把 API 地址当成死链误报。
 */
const navigationHint = /(router\.(?:push|replace)|tabs\.open|window\.open|redirect\s*:|path\s*[:=])/
const apiCall = /(?:request|axios|http)\.(?:get|post|put|delete)|fetch\(/

for (const file of collectSourceFiles(frontendRoot)) {
  if (file === routerFile) continue
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/)
  lines.forEach((line, index) => {
    if (!navigationHint.test(line) || apiCall.test(line)) return
    for (const match of line.matchAll(/'(\/[A-Za-z][A-Za-z0-9/_-]*)'/g)) {
      // 后面还有 + 说明是在拼路径前缀，交给运行时展开，不在这里判死链
      const rest = line.slice(match.index + match[0].length)
      if (/^\s*\+/.test(rest)) continue
      const target = match[1].replace(/\/+$/, '')
      if (!moduleRoots.some((root) => target.startsWith(root))) continue
      if (panelPath.test(target)) continue
      if (known.has(target)) continue
      errors.push(`${relative(file)}:${index + 1}: 跳转到不存在的路由 ${target}`)
    }
  })
}

if (errors.length) {
  console.error(`Entry audit failed (${errors.length}):`)
  for (const error of errors) console.error(`- ${error}`)
  process.exit(1)
}

console.log(`Entry audit passed: ${routes.size} 个自定义页面与入口清单一致，站内跳转目标全部有效`)
