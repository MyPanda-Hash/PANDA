#!/usr/bin/env node
/**
 * Repair UTF-8 text that was converted to literal question marks while init.sql
 * was piped through a Windows shell with the wrong encoding.
 *
 * The repair is conservative:
 * 1. Back up the target database.
 * 2. Load the repository's UTF-8 init.sql into a temporary reference database.
 * 3. Replace only target text fields containing "??" and having a matching seed row.
 * 4. Restore corrupted column/table comments and defaults from the reference schema.
 *
 * Usage:
 *   node tools/repair-db-question-marks.cjs --check [connection options]
 *   node tools/repair-db-question-marks.cjs --apply [connection options]
 *
 * Connection options default to 127.0.0.1:3306, root, light_mes. Set the
 * password with MES_DB_PASSWORD instead of putting it on the command line.
 */

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

const PROJECT_ROOT = path.resolve(__dirname, '..')
const SEED_FILE = path.join(PROJECT_ROOT, 'backend', 'src', 'main', 'resources', 'db', 'init.sql')
const TEXT_TYPES = new Set(['char', 'varchar', 'tinytext', 'text', 'mediumtext', 'longtext', 'enum', 'set', 'json'])
const NATURAL_KEYS = {
  factory: ['code'],
  form_data: ['panel_code', 'form_no'],
  manu_order: ['order_no'],
  panel_config: ['panel_code'],
  sys_role: ['role_code'],
  sys_role_panel: ['role_id', 'panel_code'],
  sys_user: ['user_name'],
}

function parseArgs(argv) {
  const out = {
    mode: '',
    host: '127.0.0.1',
    port: '3306',
    user: 'root',
    database: 'light_mes',
    mysqlBin: process.env.MYSQL_BIN || '',
    password: process.env.MES_DB_PASSWORD || '',
  }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--check' || arg === '--apply') {
      if (out.mode) throw new Error('--check 和 --apply 只能选择一个')
      out.mode = arg.slice(2)
      continue
    }
    const key = arg.startsWith('--') ? arg.slice(2) : ''
    if (!['host', 'port', 'user', 'database', 'mysql-bin'].includes(key)) {
      throw new Error(`未知参数: ${arg}`)
    }
    const value = argv[++i]
    if (!value) throw new Error(`参数 ${arg} 缺少值`)
    if (key === 'mysql-bin') out.mysqlBin = value
    else out[key] = value
  }
  if (!out.mode) throw new Error('必须指定 --check 或 --apply')
  if (!/^\d+$/.test(out.port)) throw new Error('port 必须是数字')
  for (const key of ['database', 'user']) {
    if (!/^[A-Za-z0-9_$-]+$/.test(out[key])) throw new Error(`${key} 含不支持的字符`)
  }
  return out
}

function findExecutable(name, configuredBin) {
  const exe = process.platform === 'win32' ? `${name}.exe` : name
  const candidates = []
  if (configuredBin) {
    const stat = fs.existsSync(configuredBin) ? fs.statSync(configuredBin) : null
    candidates.push(stat?.isDirectory() ? path.join(configuredBin, exe) : configuredBin)
  }
  if (process.platform === 'win32') {
    candidates.push(
      `C:\\Program Files\\MySQL\\MySQL Server 8.4\\bin\\${exe}`,
      `C:\\Program Files\\MySQL\\MySQL Server 8.0\\bin\\${exe}`,
      `C:\\Program Files\\MySQL\\MySQL Server 5.7\\bin\\${exe}`,
    )
  }
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate
  }
  const lookup = spawnSync(process.platform === 'win32' ? 'where.exe' : 'which', [exe], { encoding: 'utf8' })
  const found = lookup.status === 0 ? lookup.stdout.trim().split(/\r?\n/)[0] : ''
  if (found && fs.existsSync(found)) return found
  throw new Error(`未找到 ${exe}，请通过 --mysql-bin 指定 MySQL bin 目录`)
}

function quoteId(value) {
  return `\`${String(value).replaceAll('`', '``')}\``
}

function quoteString(value) {
  return `'${String(value).replaceAll('\\', '\\\\').replaceAll("'", "''")}'`
}

function connectionArgs(opts) {
  return [
    `--host=${opts.host}`,
    `--port=${opts.port}`,
    `--user=${opts.user}`,
    '--ssl-mode=DISABLED',
    '--default-character-set=utf8mb4',
  ]
}

function commandEnv(opts) {
  return opts.password ? { ...process.env, MYSQL_PWD: opts.password } : process.env
}

function mysqlRun(mysql, opts, sql, { raw = false } = {}) {
  const args = [...connectionArgs(opts)]
  if (raw) args.push('--batch', '--raw', '--skip-column-names')
  const result = spawnSync(mysql, args, {
    input: Buffer.from(sql, 'utf8'),
    encoding: 'utf8',
    env: commandEnv(opts),
    maxBuffer: 128 * 1024 * 1024,
  })
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || '').trim()
    throw new Error(`MySQL 执行失败${detail ? `: ${detail}` : ''}`)
  }
  return result.stdout
}

function queryRows(mysql, opts, sql) {
  const output = mysqlRun(mysql, opts, sql, { raw: true }).trim()
  if (!output) return []
  return output.split(/\r?\n/).map((line) => line.split('\t'))
}

function databaseExists(mysql, opts) {
  const name = quoteString(opts.database)
  return queryRows(mysql, opts,
    `SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=${name};`)[0]?.[0] === '1'
}

function textColumns(mysql, opts, database) {
  return queryRows(mysql, opts, `
    SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY, ORDINAL_POSITION
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=${quoteString(database)}
    ORDER BY TABLE_NAME, ORDINAL_POSITION;
  `).map(([table, column, type, key, ordinal]) => ({ table, column, type, key, ordinal: Number(ordinal) }))
}

function groupColumns(rows) {
  const tables = new Map()
  for (const row of rows) {
    if (!tables.has(row.table)) tables.set(row.table, [])
    tables.get(row.table).push(row)
  }
  return tables
}

function badExpression(alias, column) {
  return `CAST(${alias}.${quoteId(column)} AS CHAR) LIKE '%??%'`
}

function scanData(mysql, opts, database) {
  const tables = groupColumns(textColumns(mysql, opts, database))
  const statements = []
  for (const [table, columns] of tables) {
    for (const column of columns.filter((item) => TEXT_TYPES.has(item.type))) {
      statements.push(
        `SELECT ${quoteString(`${table}.${column.column}`)}, COUNT(*) ` +
        `FROM ${quoteId(database)}.${quoteId(table)} WHERE ${badExpression('x', column.column).replace('x.', '')}`,
      )
    }
  }
  if (!statements.length) return []
  return queryRows(mysql, opts, `${statements.join(' UNION ALL ')};`)
    .map(([field, count]) => ({ field, count: Number(count) }))
    .filter((item) => item.count > 0)
}

function scanSchema(mysql, opts, database) {
  const columnCount = Number(queryRows(mysql, opts, `
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=${quoteString(database)}
      AND (COLUMN_COMMENT LIKE '%?%' OR CAST(COLUMN_DEFAULT AS CHAR) LIKE '%??%');
  `)[0]?.[0] || 0)
  const tableCount = Number(queryRows(mysql, opts, `
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA=${quoteString(database)} AND TABLE_COMMENT LIKE '%?%';
  `)[0]?.[0] || 0)
  return { columnCount, tableCount }
}

function printScan(data, schema) {
  const rowCount = data.reduce((sum, item) => sum + item.count, 0)
  console.log(`[检查] 含连续问号的数据字段: ${data.length} 个，命中行次: ${rowCount}`)
  for (const item of data.slice(0, 30)) console.log(`  ${item.field}: ${item.count}`)
  if (data.length > 30) console.log(`  ... 其余 ${data.length - 30} 个字段`)
  console.log(`[检查] 污染的列定义: ${schema.columnCount}，表注释: ${schema.tableCount}`)
  return rowCount + schema.columnCount + schema.tableCount
}

function backupDatabase(mysqldump, opts) {
  const backupDir = path.join(os.tmpdir(), 'light-mes-backups')
  fs.mkdirSync(backupDir, { recursive: true })
  const stamp = new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-')
  const backupFile = path.join(backupDir, `${opts.database}-before-question-mark-repair-${stamp}.sql`)
  const args = [
    ...connectionArgs(opts),
    '--single-transaction',
    '--routines',
    '--triggers',
    `--result-file=${backupFile}`,
    opts.database,
  ]
  const result = spawnSync(mysqldump, args, {
    encoding: 'utf8',
    env: commandEnv(opts),
    maxBuffer: 16 * 1024 * 1024,
  })
  if (result.status !== 0) throw new Error(`数据库备份失败: ${(result.stderr || result.stdout || '').trim()}`)
  console.log(`[备份] ${backupFile}`)
  return backupFile
}

function loadReferenceDatabase(mysql, opts, reference) {
  let seed = fs.readFileSync(SEED_FILE, 'utf8')
  let createCount = 0
  let useCount = 0
  seed = seed.replace(/CREATE DATABASE IF NOT EXISTS\s+light_mes\b[^;]*;/i, (match) => {
    createCount++
    return match.replace(/light_mes/i, reference)
  })
  seed = seed.replace(/^USE\s+light_mes\s*;/im, () => {
    useCount++
    return `USE ${quoteId(reference)};`
  })
  if (createCount !== 1 || useCount !== 1) {
    throw new Error('init.sql 的 CREATE DATABASE/USE 结构不符合预期，已停止修复')
  }
  mysqlRun(mysql, opts, `DROP DATABASE IF EXISTS ${quoteId(reference)};\n${seed}`)
}

function joinKeys(table, columns) {
  const names = new Set(columns.map((item) => item.column))
  const primary = columns.filter((item) => item.key === 'PRI').map((item) => item.column)
  // Seed rows carry stable primary keys. Prefer them even when a natural key
  // (for example form_data.form_no) is itself one of the corrupted values.
  if (primary.length) return primary
  const natural = NATURAL_KEYS[table]
  return natural?.every((name) => names.has(name)) ? natural : []
}

function buildDataRepair(mysql, opts, reference) {
  const targetTables = groupColumns(textColumns(mysql, opts, opts.database))
  const referenceTables = groupColumns(textColumns(mysql, opts, reference))
  const statements = ['START TRANSACTION']
  for (const [table, targetColumns] of targetTables) {
    const sourceColumns = referenceTables.get(table)
    if (!sourceColumns) continue
    const sourceNames = new Set(sourceColumns.map((item) => item.column))
    const repairable = targetColumns.filter((item) => TEXT_TYPES.has(item.type) && sourceNames.has(item.column))
    const keys = joinKeys(table, targetColumns).filter((key) => sourceNames.has(key))
    if (!repairable.length || !keys.length) continue
    const join = keys.map((key) => `t.${quoteId(key)} <=> s.${quoteId(key)}`).join(' AND ')
    const bad = repairable.map((item) => badExpression('t', item.column)).join(' OR ')
    const set = repairable.map((item) => {
      const column = quoteId(item.column)
      return `t.${column}=IF(${badExpression('t', item.column)},s.${column},t.${column})`
    }).join(',')
    statements.push(
      `UPDATE ${quoteId(opts.database)}.${quoteId(table)} t ` +
      `JOIN ${quoteId(reference)}.${quoteId(table)} s ON ${join} SET ${set} WHERE ${bad}`,
    )
  }
  statements.push('COMMIT')
  return `${statements.join(';\n')};`
}

function decodeHex(value) {
  return value === '__NULL__' ? null : Buffer.from(value, 'hex').toString('utf8')
}

function columnDefault(def, dataType) {
  if (def === null) return ''
  if (/^(CURRENT_TIMESTAMP(?:\(\d+\))?|CURRENT_DATE|CURRENT_TIME)$/i.test(def)) return ` DEFAULT ${def}`
  if (/^(tinyint|smallint|mediumint|int|bigint|decimal|numeric|float|double|bit|year)$/i.test(dataType)) {
    return ` DEFAULT ${def}`
  }
  return ` DEFAULT ${quoteString(def)}`
}

function buildSchemaRepair(mysql, opts, reference) {
  const rows = queryRows(mysql, opts, `
    SELECT s.TABLE_NAME, s.COLUMN_NAME, s.COLUMN_TYPE, s.DATA_TYPE, s.IS_NULLABLE,
           IF(s.COLUMN_DEFAULT IS NULL,'__NULL__',HEX(s.COLUMN_DEFAULT)),
           IFNULL(s.CHARACTER_SET_NAME,''), IFNULL(s.COLLATION_NAME,''),
           IFNULL(s.EXTRA,''), HEX(s.COLUMN_COMMENT)
    FROM information_schema.COLUMNS t
    JOIN information_schema.COLUMNS s
      ON s.TABLE_SCHEMA=${quoteString(reference)}
     AND s.TABLE_NAME=t.TABLE_NAME AND s.COLUMN_NAME=t.COLUMN_NAME
    WHERE t.TABLE_SCHEMA=${quoteString(opts.database)}
      AND (t.COLUMN_COMMENT LIKE '%?%' OR CAST(t.COLUMN_DEFAULT AS CHAR) LIKE '%??%')
    ORDER BY s.TABLE_NAME, s.ORDINAL_POSITION;
  `)
  const byTable = new Map()
  for (const row of rows) {
    const [table, column, columnType, dataType, nullable, defaultHex, charset, collation, extra, commentHex] = row
    let definition = `${quoteId(column)} ${columnType}`
    if (charset) definition += ` CHARACTER SET ${charset}`
    if (collation) definition += ` COLLATE ${collation}`
    definition += nullable === 'NO' ? ' NOT NULL' : ' NULL'
    definition += columnDefault(decodeHex(defaultHex), dataType)
    const cleanExtra = extra.replace(/\bDEFAULT_GENERATED\b/gi, '').replace(/\s+/g, ' ').trim()
    if (cleanExtra) definition += ` ${cleanExtra}`
    definition += ` COMMENT ${quoteString(decodeHex(commentHex) || '')}`
    if (!byTable.has(table)) byTable.set(table, [])
    byTable.get(table).push(`MODIFY COLUMN ${definition}`)
  }
  const statements = []
  for (const [table, definitions] of byTable) {
    statements.push(`ALTER TABLE ${quoteId(opts.database)}.${quoteId(table)} ${definitions.join(',')}`)
  }
  const tableRows = queryRows(mysql, opts, `
    SELECT s.TABLE_NAME, HEX(s.TABLE_COMMENT)
    FROM information_schema.TABLES t
    JOIN information_schema.TABLES s
      ON s.TABLE_SCHEMA=${quoteString(reference)} AND s.TABLE_NAME=t.TABLE_NAME
    WHERE t.TABLE_SCHEMA=${quoteString(opts.database)} AND t.TABLE_COMMENT LIKE '%?%';
  `)
  for (const [table, commentHex] of tableRows) {
    statements.push(
      `ALTER TABLE ${quoteId(opts.database)}.${quoteId(table)} COMMENT=${quoteString(decodeHex(commentHex) || '')}`,
    )
  }
  return statements.length ? `${statements.join(';\n')};` : ''
}

function validateReference(mysql, opts, reference) {
  const bad = scanData(mysql, opts, reference)
  if (bad.length) {
    throw new Error(`init.sql 对照库自身仍有 ${bad.length} 个连续问号字段，拒绝用它覆盖当前数据`)
  }
  const invalidJson = Number(queryRows(mysql, opts, `
    SELECT COUNT(*) FROM ${quoteId(reference)}.panel_config WHERE config IS NOT NULL AND NOT JSON_VALID(config);
  `)[0]?.[0] || 0)
  if (invalidJson) throw new Error(`init.sql 对照库包含 ${invalidJson} 条无效面板 JSON，已停止修复`)
}

function main() {
  const opts = parseArgs(process.argv.slice(2))
  const mysql = findExecutable('mysql', opts.mysqlBin)
  if (!databaseExists(mysql, opts)) throw new Error(`数据库不存在: ${opts.database}`)

  const beforeData = scanData(mysql, opts, opts.database)
  const beforeSchema = scanSchema(mysql, opts, opts.database)
  const beforeProblems = printScan(beforeData, beforeSchema)
  if (opts.mode === 'check') process.exitCode = beforeProblems ? 2 : 0
  if (opts.mode !== 'apply' || !beforeProblems) return

  const mysqldump = findExecutable('mysqldump', opts.mysqlBin)
  backupDatabase(mysqldump, opts)
  const reference = `${opts.database}_utf8_repair`
  if (!/^[A-Za-z0-9_$]+$/.test(reference) || reference === opts.database || reference.length > 64) {
    throw new Error('临时数据库名称不安全，已停止修复')
  }

  let referenceCreated = false
  try {
    console.log(`[对照库] 正在从 UTF-8 init.sql 构建 ${reference}`)
    loadReferenceDatabase(mysql, opts, reference)
    referenceCreated = true
    validateReference(mysql, opts, reference)
    mysqlRun(mysql, opts, buildDataRepair(mysql, opts, reference))
    const schemaRepair = buildSchemaRepair(mysql, opts, reference)
    if (schemaRepair) mysqlRun(mysql, opts, schemaRepair)

    const afterData = scanData(mysql, opts, opts.database)
    const afterSchema = scanSchema(mysql, opts, opts.database)
    const afterProblems = printScan(afterData, afterSchema)
    if (afterProblems) throw new Error('仍有无法由 init.sql 自动匹配的问号污染，请根据备份人工核对')
    console.log('[完成] 数据、面板配置和数据库定义中的问号污染已修复')
  } finally {
    if (referenceCreated) mysqlRun(mysql, opts, `DROP DATABASE IF EXISTS ${quoteId(reference)};`)
  }
}

try {
  main()
} catch (error) {
  console.error(`[失败] ${error.message}`)
  process.exitCode = 1
}
