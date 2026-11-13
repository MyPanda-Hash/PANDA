#!/usr/bin/env node
/**
 * 迁移对账：MySQL（回滚库）与 PostgreSQL（主库）逐表比对。
 *
 * 比对三件事：
 *   1. 每张表行数相等
 *   2. 关键金额 / 数量列合计相等（浮点按分位比较，避免 decimal 表示差异）
 *   3. identity 序列不落后于 max(id)（落后会在插入时撞主键）
 *
 * 用法（工作区根目录）：
 *   set -a && . ./.env.local && set +a
 *   node light-mes/tools/pg-parity.mjs
 */
import { execFileSync } from 'node:child_process'

const MYSQL = process.env.MES_MYSQL_CONTAINER || 'light-mes-db'
const PG = process.env.MES_PG_CONTAINER || 'light-mes-pg'
const DB = process.env.MES_DB_NAME || 'light_mes'
const PWD = process.env.SPRING_DATASOURCE_PASSWORD
if (!PWD) throw new Error('缺少 SPRING_DATASOURCE_PASSWORD（先 source .env.local）')

const mysql = sql => execFileSync('docker', [
  'exec', '-i', '-e', `MYSQL_PWD=${PWD}`, MYSQL,
  'mysql', '-uroot', '-N', '-B', '--default-character-set=utf8mb4', DB, '-e', sql,
], { encoding: 'utf8' }).trim()

const psql = sql => execFileSync('docker', [
  'exec', '-i', PG, 'psql', '-U', 'postgres', '-d', DB, '-t', '-A', '-v', 'ON_ERROR_STOP=1', '-c', sql,
], { encoding: 'utf8' }).trim()

const lines = out => out.split('\n').filter(Boolean).map(l => l.split('\t'))

const tables = mysql("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type='BASE TABLE' ORDER BY table_name;")
  .split('\n').filter(Boolean)

const problems = []
const n = v => Number(v || 0)

// 1. 行数。运行中的应用会持续写日志表，只要求 PG 不少于 MySQL
const LIVE_TABLES = new Set(['sys_usage_log', 'sys_backup_log'])
for (const t of tables) {
  const a = n(mysql(`SELECT COUNT(*) FROM ${t};`))
  const b = n(psql(`SELECT COUNT(*) FROM ${t};`))
  const ok = LIVE_TABLES.has(t) ? b >= a : a === b
  if (!ok) problems.push(`行数不一致 ${t}: mysql=${a} pg=${b}`)
}

// 2. 金额 / 数量合计
const SUMS = [
  ['stock_balance', 'quantity'], ['stock_balance', 'amount'], ['stock_balance', 'locked_quantity'],
  ['stock_txn', 'quantity'], ['stock_txn', 'amount'],
  ['accounting_entry', 'amount'], ['finance_write_off', 'amount'],
  ['form_approval', 'id'],
]
const same = (a, b, tol) => Math.abs(a - b) <= tol
for (const [t, col] of SUMS) {
  const a = n(mysql(`SELECT COALESCE(SUM(${col}),0) FROM ${t};`))
  const b = n(psql(`SELECT COALESCE(SUM(${col}),0) FROM ${t};`))
  const tol = col === 'id' ? 0 : 0.01
  if (!same(a, b, tol)) problems.push(`合计不一致 ${t}.${col}: mysql=${a} pg=${b}`)
}

// 3. 序列健康：nextval 必须 > max(id)
for (const t of tables) {
  const hasId = psql(`SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='${t}' AND column_name='id' AND is_identity='YES';`)
  if (n(hasId) === 0) continue
  const row = psql(`SELECT COALESCE(MAX(id),0) FROM ${t};`)
  const seqName = psql(`SELECT pg_get_serial_sequence('${t}','id');`)
  if (!seqName) continue
  const [last, called] = psql(`SELECT last_value, is_called FROM ${seqName};`).split('|')
  const next = called === 't' ? n(last) + 1 : n(last)
  if (next <= n(row)) problems.push(`序列落后 ${t}: nextval=${next} max(id)=${row}`)
}

if (problems.length) {
  console.error(`对账失败（${problems.length} 项）：`)
  for (const p of problems) console.error('- ' + p)
  process.exit(1)
}
console.log(`对账通过：${tables.length} 张表行数一致，${SUMS.length} 项金额合计一致，identity 序列全部领先 max(id)`)
