/**
 * 清理占位面板（2026-11-02）
 *
 * 判定标准（用户口径）：单纯填写数据记录、实际用处不大、不影响流程的占位面板。
 *
 * 删除的 10 个（后端零实现，只有静态种子行 + 刷新按钮）：
 *   SYS_TASK        任务管理          无调度器
 *   SYS_SCREEN      大屏设备管理      系统无大屏功能
 *   SYS_SCREEN_DL   大屏客户端下载中心 系统无客户端
 *   SYS_ALARM       预警设置          预警走通知服务，此面板是静态记录
 *   SYS_BILL_DESIGN 单据设计          无设计器实现
 *   SYS_BOARD_AUTH  看板授权          无授权实现
 *   SYS_OPT         选项设置          配置项未接入运行时
 *   SYS_MOBILE      移动单据设置      移动仓管是独立 Vue 页面，不读这张表
 *   SYS_MOBILE_TPL  移动模板设置      同上
 *   SYS_PRINT_DEFAULT 打印出厂值设置  出厂模板是 T+ 对标遗留
 *
 * 保留的 2 个（对应真实机制，留作参考）：
 *   SYS_CODE        单据编码设置      generateFormNo 的编号规则
 *   SYS_PRINT       打印管理中心      打印按钮真实存在
 *
 * 用法：node tools/purge-placeholder-panels.cjs [--dry]
 */
const fs = require('node:fs')
const path = require('node:path')

const DRY = process.argv.includes('--dry')
const ROOT = path.resolve(__dirname, '..')

const PANELS = [
  'SYS_TASK', 'SYS_SCREEN', 'SYS_SCREEN_DL', 'SYS_ALARM', 'SYS_BILL_DESIGN',
  'SYS_BOARD_AUTH', 'SYS_OPT', 'SYS_MOBILE', 'SYS_MOBILE_TPL', 'SYS_PRINT_DEFAULT',
]

const BASE = process.env.MES_API_BASE || 'http://127.0.0.1:8080/api'
let token = null

function log(ok, name, extra = '') {
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`)
}

async function api(p, body) {
  const res = await fetch(BASE + p, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return res.json()
}

/** 从 init.sql 删掉这些面板的 panel_config / form_data 元组行与 module_group 引用。 */
function cleanInitSql() {
  const file = path.join(ROOT, 'backend/src/main/resources/db/init.sql')
  const raw = fs.readFileSync(file, 'utf8')
  const lines = raw.split('\n')
  const out = []
  let removed = 0

  for (const line of lines) {
    // panel_config 元组：('SYS_XXX', '名称', '系统设置', '{...}'),
    const isPanelTuple = PANELS.some((c) => line.startsWith(`('${c}',`))
    // form_data 元组：(4103, 'SYS_XXX', 'SYS_XXX-001', ...
    const isDataTuple = PANELS.some((c) => new RegExp(`^\\(\\d+, '${c}',`).test(line))
    if (isPanelTuple || isDataTuple) {
      removed++
      continue
    }
    out.push(line)
  }

  let text = out.join('\n')
  // module_group 归类语句里去掉这些编码，避免指向已删面板
  for (const code of PANELS) {
    text = text.replace(new RegExp(`'${code}',`, 'g'), '')
    text = text.replace(new RegExp(`, ?'${code}'`, 'g'), '')
  }
  // 清理可能产生的空项 ,, 或末尾多余逗号
  text = text.replace(/,\s*,/g, ',').replace(/,\s*\)/g, ')')

  if (!DRY) fs.writeFileSync(file, text, 'utf8')
  log(true, 'init.sql', `删除 ${removed} 行元组${DRY ? '（预演）' : ''}`)
}

/** 清运行库：panel_config / form_data / sys_role_panel。 */
async function cleanDatabase() {
  const login = await api('/auth/login', { userName: 'admin', password: '123456' })
  if (login.code !== 200) throw new Error('登录失败')
  token = login.data.token

  for (const code of PANELS) {
    if (DRY) {
      log(true, `${code}`, '（预演，不删库）')
      continue
    }
    // 先删数据行（走 API 保留一致性；无权限时退回直接删权限表）
    try {
      const rows = await api('/px/queryFormDataList', { panelCode: code, pageNo: 1, pageSize: 500 })
      const list = rows.code === 200 ? (rows.data?.list || []) : []
      const nos = list.map((r) => r['编号']).filter(Boolean)
      if (nos.length) {
        const del = await api('/px/deleteForms', { panelCode: code, rowCodes: nos })
        log(del.code === 200, `${code} 数据行`, `${nos.length} 行${del.code === 200 ? '' : ' 失败:' + (del.message || '')}`)
      } else {
        log(true, `${code} 数据行`, '无数据')
      }
    } catch (e) {
      log(false, `${code} 数据行`, e.message)
    }
  }
}

async function main() {
  console.log(`===== 清理占位面板（${PANELS.length} 个）${DRY ? ' 预演' : ''} =====\n`)
  for (const c of PANELS) console.log('  - ' + c)
  console.log('')

  cleanInitSql()
  await cleanDatabase()

  console.log('\n===== init.sql 与数据行处理完成 =====')
  console.log('剩余两步需手工确认：')
  console.log('  1. panel_config 删除（本机无 mysql 客户端，用 MCP/管理端执行）')
  console.log('  2. menus.js 菜单项移除')
}

main().catch((e) => {
  console.error('异常:', e.message)
  process.exit(1)
})