// 生成服务器部署用 SQL v2：本机全量业务数据 + 本机全部面板 + init.sql 修复版面板覆盖
// 用法: node tools/build-deploy-sql.mjs
// 输出: docs/deploy/light_mes_deploy.sql
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const baseFile = path.join(root, 'docs/deploy/base.sql')
const localPanelsFile = path.join(root, 'docs/deploy/local-panels.sql')
const initFile = path.join(root, 'backend/src/main/resources/db/init.sql')
const outFile = path.join(root, 'docs/deploy/light_mes_deploy.sql')

// 提取 SQL 中所有含指定表名 INSERT 的完整语句（扫描到语句真正结束：末尾分号且不在引号内）
function extractStmts(sqlText, tableToken) {
  const stmts = []
  const lines = sqlText.split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (!line.includes(tableToken) || !/^\s*(INSERT INTO|REPLACE INTO)/.test(line)) continue
    const buf = [line]
    while (!isStmtEnd(buf.join('\n'))) {
      i++
      if (i >= lines.length) throw new Error(tableToken + ' INSERT 未以分号结束（第 ' + i + ' 行）')
      buf.push(lines[i])
    }
    stmts.push(buf.join('\n').trim())
  }
  return stmts
}

function isStmtEnd(text) {
  const t = text.trimEnd()
  if (!t.endsWith(';')) return false
  let inS = false, inD = false
  for (let i = 0; i < t.length - 1; i++) {
    const c = t[i]
    if (c === '\\') { i++; continue }
    if (c === "'" && !inD) inS = !inS
    else if (c === '"' && !inS) inD = !inD
  }
  return !inS && !inD
}

// 统计扩展 INSERT 的 VALUES 行数，兼容 (1,'CODE', 与 ('CODE', 两种格式
function countValues(stmt) {
  const re = /\((\d+,)?'[A-Z0-9_]+',/g
  return (stmt.match(re) || []).length
}

// 1. base（本机 10 表业务数据，无 panel_config）
const base = fs.readFileSync(baseFile, 'utf8')
if (!base.includes('CREATE DATABASE') || base.includes('panel_config')) {
  throw new Error('base.sql 异常：应含建库语句且不含 panel_config')
}

// 2. 本机全部面板：建表 + INSERT
const localPanels = fs.readFileSync(localPanelsFile, 'utf8')
const createMatch = localPanels.match(/CREATE TABLE `?panel_config`?[\s\S]*?ENGINE[^;]*;/)
if (!createMatch) throw new Error('local-panels.sql 未找到 panel_config 建表语句')
const createTable = createMatch[0].replace(/^CREATE TABLE/, 'CREATE TABLE IF NOT EXISTS')
const localInserts = extractStmts(localPanels, 'panel_config')
const localCount = localInserts.reduce((n, s) => n + countValues(s), 0)
if (localCount < 50) throw new Error('本机面板 VALUES 异常：' + localCount)

// 3. init.sql 修复版面板（ON DUPLICATE 覆盖）
const init = fs.readFileSync(initFile, 'utf8')
const initInserts = extractStmts(init, 'panel_config').filter(s => s.includes('ON DUPLICATE KEY UPDATE'))
const initCount = initInserts.reduce((n, s) => n + countValues(s), 0)
if (initCount < 40) throw new Error('init.sql 面板 VALUES 异常：' + initCount)

// 4. 合并
const header = '-- light_mes 服务器部署专用 SQL（自动生成，勿手改）\n' +
  '-- 内容：本机全量业务数据（10 张表）+ 本机全部面板（' + localCount + ' 个）\n' +
  '--       + init.sql 最新面板覆盖（' + initCount + ' 个，修复库存 6 单据嵌套数组等结构问题）\n' +
  '-- 生成时间: ' + new Date().toISOString() + '\n' +
  '-- 导入方式: mysql -uroot -p < light_mes_deploy.sql\n'
const out = header + '\n' + base.trimEnd() + '\n\n-- ===== panel_config（本机全部） =====\n' + createTable + '\n' +
  localInserts.join('\n') + '\n\n-- ===== panel_config 覆盖（init.sql 修复版） =====\n' +
  initInserts.join('\n') + '\n'
fs.writeFileSync(outFile, out, 'utf8')
console.log('OK: ' + outFile + ' (' + Math.round(out.length / 1024) + ' KB)')
console.log('  业务: ' + Math.round(base.length / 1024) + ' KB, 本机面板: ' + localCount + ', init覆盖: ' + initCount)