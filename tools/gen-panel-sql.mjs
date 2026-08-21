#!/usr/bin/env node
/**
 * gen-panel-sql.mjs
 * 从 frontend/src/business/engine.js 的 REPORT_CONFIGS / PROD_REPORT_CONFIGS
 * 提取 28 个报表面板（16 库存核算 + 12 生产/销售）配置，生成 panel_config 的 SQL。
 *
 * 输出：
 *   backend/src/main/resources/db/panel_report_config.sql  —— 增量（仅报表面板 INSERT）
 *   backend/src/main/resources/db/init_full.sql            —— 完整（init.sql + 报表面板 INSERT）
 *
 * 用法：node tools/gen-panel-sql.mjs
 */
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(__dirname, '..')
const ENGINE = join(ROOT, 'frontend', 'src', 'business', 'engine.js')
const INIT_SQL = join(ROOT, 'backend', 'src', 'main', 'resources', 'db', 'init.sql')
const OUT_PANELS = join(ROOT, 'backend', 'src', 'main', 'resources', 'db', 'panel_report_config.sql')
const OUT_FULL = join(ROOT, 'backend', 'src', 'main', 'resources', 'db', 'init_full.sql')

// ---- 1. 读取 engine.js，按行号提取片段（含端点）----
const lines = readFileSync(ENGINE, 'utf8').split(/\r?\n/)
const slice = (start, end) => lines.slice(start - 1, end).join('\n').replace(/^\uFEFF/, '')

const PARTS = [
  // 基础常量
  slice(57, 57),                        // WORKSHOP_OPTIONS
  slice(9486, 9486),                    // REPORT_NUM_RE
  slice(9489, 9493),                    // REPORT_TOOLBAR
  slice(9496, 9503),                    // COST_TOOLBAR
  slice(9506, 9510),                    // RPT_Q_WAREHOUSE/SUPPLIER/CUSTOMER/INV/MATERIAL
  slice(9511, 9515),                    // RPT_Q_BASE
  // reportPanel 工厂函数（L9518-9545）
  slice(9518, 9545),
  // 16 个库存核算报表面板
  slice(9724, 9805),                    // const REPORT_CONFIGS = {...}
  // 生产/销售报表查询字段常量
  slice(9869, 9872),                    // RPT_Q_WORKSHOP/PROC/WORKER/STATUS
  // 12 个生产/销售报表面板
  slice(10054, 10115),                  // const PROD_REPORT_CONFIGS = {...}
  'Object.assign(REPORT_CONFIGS, PROD_REPORT_CONFIGS)',
  'module.exports = { REPORT_CONFIGS }',
].join('\n\n')

// ---- 2. 求值 ----
const tmpDir = mkdtempSync(join(tmpdir(), 'gen-panel-sql-'))
const modFile = join(tmpDir, 'panels.cjs')
try {
  writeFileSync(modFile, PARTS, 'utf8')
  const require = createRequire(import.meta.url)
  const { REPORT_CONFIGS } = require(modFile)
  const codes = Object.keys(REPORT_CONFIGS)
  if (codes.length !== 28) {
    console.error(`[警告] 期望 28 个报表面板，实际 ${codes.length} 个: ${codes.join(', ')}`)
  } else {
    console.log(`[OK] 提取到 ${codes.length} 个报表面板`)
  }

  // ---- 3. 生成 SQL ----
  const esc = (s) => String(s).replace(/'/g, "''")
  const rows = codes.map((code) => {
    const cfg = REPORT_CONFIGS[code]
    if (!cfg || !cfg.metadata) throw new Error(`面板 ${code} 缺少 metadata`)
    const name = cfg.metadata.panelName ?? code
    const category = cfg.metadata.panelCategory ?? '报表'
    const json = JSON.stringify(cfg)
    return `('${esc(code)}', '${esc(name)}', '${esc(category)}',\n'${esc(json)}')`
  })

  const header = `-- 报表面板配置（28 个：16 库存核算 + 12 生产/销售）
-- 由 tools/gen-panel-sql.mjs 从 frontend/src/business/engine.js 自动生成，请勿手改
-- 生成时间：${new Date().toISOString()}
INSERT INTO panel_config (panel_code, panel_name, category, config) VALUES\n${rows.join(',\n')}\nON DUPLICATE KEY UPDATE panel_name = VALUES(panel_name), category = VALUES(category), config = VALUES(config);\n`

  writeFileSync(OUT_PANELS, header, 'utf8')
  console.log(`[OK] 增量 SQL 已写入: ${OUT_PANELS} (${Buffer.byteLength(header, 'utf8')} bytes)`)

  // ---- 4. 完整版：init.sql + 报表面板 ----
  const initRaw = readFileSync(INIT_SQL, 'utf8')
  if (initRaw.includes('\uFFFD')) {
    console.error('[错误] init.sql 存在编码替换符（非 UTF-8？），中止生成完整版')
    process.exit(1)
  }
  const sep = '\n\n-- ==================== 报表面板配置（生成器补齐，见 tools/gen-panel-sql.mjs） ====================\n\n'
  const full = initRaw.replace(/\r?\n$/, '') + sep + header
  writeFileSync(OUT_FULL, full, 'utf8')
  console.log(`[OK] 完整 SQL 已写入: ${OUT_FULL} (${Buffer.byteLength(full, 'utf8')} bytes)`)

  // ---- 5. 自检：JSON 合法性 ----
  let bad = 0
  for (const code of codes) {
    try { JSON.parse(JSON.stringify(REPORT_CONFIGS[code])) } catch { bad++; console.error(`[错误] ${code} JSON 非法`) }
  }
  console.log(bad === 0 ? '[OK] 全部 config JSON 合法' : `[失败] ${bad} 个面板 JSON 非法`)
} finally {
  rmSync(tmpDir, { recursive: true, force: true })
}
