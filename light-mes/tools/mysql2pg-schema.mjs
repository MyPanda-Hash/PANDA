#!/usr/bin/env node
/**
 * 从 MySQL 运行库生成 PostgreSQL 建表语句（Flyway V1 baseline）。
 *
 * 为什么以运行库为准：init.sql 只建 19 张表，运行库有 26 张，其余由
 * db/migrate_*.sql 与历史 SchemaInit 在运行时补齐；手翻 24 个 SQL 文件必然漂移，
 * 所以结构权威源取 information_schema，产物落一份、进 Flyway。
 *
 * 用法（工作区根目录）：
 *   set -a && . ./.env.local && set +a
 *   node light-mes/tools/mysql2pg-schema.mjs [输出路径]
 *
 * 默认输出 light-mes/backend/src/main/resources/db/migration/V1__baseline.sql
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

const CONTAINER = process.env.MES_MYSQL_CONTAINER || 'light-mes-db'
const DB = process.env.MES_DB_NAME || 'light_mes'
const OUT = process.argv[2] || 'light-mes/backend/src/main/resources/db/migration/V1__baseline.sql'

/** 实体里映射成 Boolean 的列 → PG boolean；其余 TINYINT 保持 smallint，避免 MyBatis 参数类型不匹配 */
const BOOLEAN_COLUMNS = new Set([
  'stock_balance.ext_flag1', 'stock_balance.ext_flag2', 'stock_balance.ext_flag3',
  'stock_balance.ext_flag4', 'stock_balance.ext_flag5',
  'form_data.current_version',
])

function mysqlJson(sql) {
  const password = process.env.SPRING_DATASOURCE_PASSWORD
  if (!password) throw new Error('缺少 SPRING_DATASOURCE_PASSWORD（先 source .env.local）')
  const out = execFileSync('docker', [
    'exec', '-i', '-e', `MYSQL_PWD=${password}`, CONTAINER,
    'mysql', '-uroot', '-N', '-B', '--default-character-set=utf8mb4', '-e', sql,
  ], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
  const text = out.trim()
  if (!text || text === 'NULL') return []
  return JSON.parse(text)
}

const columns = mysqlJson(`
  SELECT JSON_ARRAYAGG(JSON_OBJECT(
    'table', TABLE_NAME, 'column', COLUMN_NAME, 'pos', ORDINAL_POSITION,
    'type', COLUMN_TYPE, 'nullable', IS_NULLABLE, 'default', COLUMN_DEFAULT,
    'extra', EXTRA, 'comment', COLUMN_COMMENT))
  FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '${DB}'`)

const indexes = mysqlJson(`
  SELECT JSON_ARRAYAGG(JSON_OBJECT(
    'table', TABLE_NAME, 'index', INDEX_NAME, 'nonUnique', NON_UNIQUE,
    'seq', SEQ_IN_INDEX, 'column', COLUMN_NAME))
  FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = '${DB}'`)

const tables = mysqlJson(`
  SELECT JSON_ARRAYAGG(JSON_OBJECT('table', TABLE_NAME, 'comment', TABLE_COMMENT))
  FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = '${DB}' AND TABLE_TYPE = 'BASE TABLE'`)

const tableNames = tables.map(t => t.table).sort()
const tableComment = new Map(tables.map(t => [t.table, t.comment || '']))

function pgType(col) {
  const t = col.type.toLowerCase()
  const key = `${col.table}.${col.column}`
  if (t.startsWith('bigint')) return { sql: 'bigint', kind: 'int' }
  if (t === 'int' || t === 'integer') return { sql: 'integer', kind: 'int' }
  if (t.startsWith('smallint')) return { sql: 'smallint', kind: 'int' }
  if (t.startsWith('tinyint')) {
    return BOOLEAN_COLUMNS.has(key) ? { sql: 'boolean', kind: 'boolean' } : { sql: 'smallint', kind: 'int' }
  }
  if (t.startsWith('varchar')) return { sql: t, kind: 'text' }
  if (t.startsWith('char(')) return { sql: t, kind: 'text' }
  if (t.startsWith('decimal')) return { sql: t.replace('decimal', 'numeric'), kind: 'numeric' }
  if (t === 'datetime') return { sql: 'timestamp(0)', kind: 'timestamp' }
  if (t === 'date') return { sql: 'date', kind: 'date' }
  if (t === 'text' || t === 'mediumtext' || t === 'longtext') return { sql: 'text', kind: 'text' }
  if (t === 'json') return { sql: 'jsonb', kind: 'text' }
  throw new Error(`未覆盖的 MySQL 类型：${col.table}.${col.column} ${col.type}`)
}

const q = s => `'${String(s).replace(/'/g, "''")}'`

function defaultValue(col, kind) {
  const raw = col.default
  if (raw === null || raw === undefined) return ''
  if (/CURRENT_TIMESTAMP/i.test(raw)) return ' default now()'
  if (kind === 'boolean') return ` default ${raw === '1' ? 'true' : 'false'}`
  if (kind === 'int' || kind === 'numeric') return ` default ${raw}`
  if (kind === 'date' || kind === 'timestamp') return ` default ${q(raw)}`
  return ` default ${q(raw)}`
}

const lines = []
lines.push('-- 轻MES PostgreSQL 结构基线（Flyway V1）')
lines.push('-- 由 light-mes/tools/mysql2pg-schema.mjs 从运行库 information_schema 生成，勿手改。')
lines.push(`-- 生成时间：${new Date().toISOString().slice(0, 10)}；来源：MySQL ${DB}（${tableNames.length} 表 / ${columns.length} 列 / ${indexes.length} 索引项）`)
lines.push('')

const indexByTable = new Map()
for (const ix of indexes) {
  if (!indexByTable.has(ix.table)) indexByTable.set(ix.table, new Map())
  const m = indexByTable.get(ix.table)
  if (!m.has(ix.index)) m.set(ix.index, { name: ix.index, unique: ix.nonUnique === 0, cols: [] })
  m.get(ix.index).cols.push({ seq: Number(ix.seq), column: ix.column })
}

const touchedTables = []

for (const table of tableNames) {
  const cols = columns.filter(c => c.table === table).sort((a, b) => Number(a.pos) - Number(b.pos))
  lines.push(`-- ${tableComment.get(table) || table}`)
  lines.push(`create table if not exists ${table} (`)
  const body = cols.map(col => {
    const { sql, kind } = pgType(col)
    const identity = /auto_increment/i.test(col.extra || '') ? ' generated by default as identity' : ''
    const nullable = col.nullable === 'NO' ? ' not null' : ''
    const def = identity ? '' : defaultValue(col, kind)
    return { text: `  ${col.column} ${sql}${identity}${nullable}${def}`, col }
  })
  const pk = indexByTable.get(table)?.get('PRIMARY')
  const parts = body.map(b => b.text)
  if (pk) parts.push(`  constraint pk_${table} primary key (${pk.cols.sort((a, b) => a.seq - b.seq).map(c => c.column).join(', ')})`)
  for (const ix of indexByTable.get(table)?.values() || []) {
    if (!ix.unique || ix.name === 'PRIMARY') continue
    // PG 约束名是全 schema 唯一，MySQL 索引名是表内唯一 → 前缀表名
    parts.push(`  constraint uk_${table}_${ix.name.replace(/^uk_/, '')} unique (${ix.cols.sort((a, b) => a.seq - b.seq).map(c => c.column).join(', ')})`)
  }
  lines.push(parts.join(',\n'))
  lines.push(');')
  for (const ix of indexByTable.get(table)?.values() || []) {
    if (ix.unique || ix.name === 'PRIMARY') continue
    const idxName = ix.name.startsWith(`idx_${table}`) ? ix.name : `idx_${table}_${ix.name.replace(/^idx_/, '')}`
    lines.push(`create index if not exists ${idxName} on ${table} (${ix.cols.sort((a, b) => a.seq - b.seq).map(c => c.column).join(', ')});`)
  }
  lines.push('')
  if (tableComment.get(table)) lines.push(`comment on table ${table} is ${q(tableComment.get(table))};`)
  for (const col of cols) {
    if (!col.comment) continue
    lines.push(`comment on column ${table}.${col.column} is ${q(col.comment)};`)
  }
  if (cols.some(c => /on update CURRENT_TIMESTAMP/i.test(c.extra || ''))) touchedTables.push(table)
  lines.push('')
}

if (touchedTables.length) {
  lines.push('-- MySQL 的 ON UPDATE CURRENT_TIMESTAMP 在 PG 没有等价语法，用 BEFORE UPDATE 触发器补齐')
  lines.push('create or replace function touch_update_time() returns trigger language plpgsql as $$')
  lines.push('begin')
  lines.push('  new.update_time := clock_timestamp();')
  lines.push('  return new;')
  lines.push('end $$;')
  lines.push('')
  for (const table of touchedTables) {
    lines.push(`drop trigger if exists trg_${table}_touch on ${table};`)
    lines.push(`create trigger trg_${table}_touch before update on ${table} for each row execute function touch_update_time();`)
  }
  lines.push('')
}

const outPath = path.resolve(OUT)
mkdirSync(path.dirname(outPath), { recursive: true })
writeFileSync(outPath, lines.join('\n'), 'utf8')

const identityCount = columns.filter(c => /auto_increment/i.test(c.extra || '')).length
const commentCount = columns.filter(c => c.comment).length + tables.filter(t => t.comment).length
console.log(JSON.stringify({
  out: outPath,
  tables: tableNames.length,
  columns: columns.length,
  indexes: indexes.length,
  identity: identityCount,
  comments: commentCount,
  triggers: touchedTables.length,
}, null, 2))
