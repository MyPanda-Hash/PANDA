/**
 * 以某个账号的视角查看（仅管理员）。
 *
 * 为什么要有它：
 * - 之前只有「管理者/车间主任/一线员工」三个固定角色，切换后只换了文案，
 *   数据仍按当前登录人取，于是出现「管理员权限 + 别人口径」的混合，看着很乱；
 * - 现在改成选**具体账号**：后端按那个账号的身份取数（view_as），
 *   前端视图可见性也按那个账号的角色算，两边一致。
 *
 * 安全：view_as 只对管理员生效；普通账号传了会被后端 403。
 */
import { VIEW_AS } from './api.js'
import { toast } from './util.js'

const STORAGE_KEY = 'dnx-view-as'

/** 当前查看身份；null 表示「用我自己的身份」。 */
export function viewIdentity() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && parsed.username ? parsed : null
  } catch (_) {
    return null
  }
}

/** 请求要带的 view_as 参数（空串表示本人）。 */
export function viewAsParam() {
  const identity = viewIdentity()
  return identity ? identity.username : ''
}

/** 角色映射：后端给的角色码 → 工作台的三种视角。 */
export function roleOfAccount(account) {
  if (!account) return ''
  if (account.isAdmin) return 'manager'
  const role = String(account.role || '').toUpperCase()
  if (['FOREMAN', 'WORKER', 'PLANNER'].includes(role)) return 'supervisor'
  return 'staff'
}

export function clearViewIdentity() {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch (_) {
    /* 隐私模式下不持久化 */
  }
}

/**
 * 初始化顶栏的账号选择器。只有管理员会看到它。
 * 返回 true 表示挂上了。
 */
export async function initViewAsSwitch({ isAdmin, onSwitch }) {
  const wrap = document.getElementById('roleSwitchWrap')
  const select = document.getElementById('roleSwitch')
  if (!wrap || !select) return false
  if (!isAdmin) {
    wrap.hidden = true
    clearViewIdentity()
    return false
  }

  let accounts = []
  try {
    const payload = await VIEW_AS.accounts()
    const data = (payload && payload.data) || payload
    accounts = Array.isArray(data) ? data : []
  } catch (_) {
    wrap.hidden = true
    return false
  }

  if (!accounts.length) {
    wrap.hidden = true
    return false
  }

  select.replaceChildren()
  const self = document.createElement('option')
  self.value = ''
  self.textContent = '我的视角'
  select.append(self)
  for (const account of accounts) {
    const opt = document.createElement('option')
    opt.value = account.username
    opt.textContent = (account.displayName || account.username) + (account.isAdmin ? '（管理员）' : '')
    select.append(opt)
  }

  const current = viewIdentity()
  select.value = current ? current.username : ''
  wrap.hidden = false

  select.onchange = async () => {
    const picked = accounts.find((item) => item.username === select.value)
    try {
      if (picked) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          username: picked.username,
          displayName: picked.displayName || picked.username,
          role: roleOfAccount(picked),
        }))
        toast('正在以「' + (picked.displayName || picked.username) + '」的视角查看')
      } else {
        clearViewIdentity()
        toast('已切回我的视角')
      }
    } catch (_) {
      /* 隐私模式下仅本次生效 */
    }
    if (typeof onSwitch === 'function') await onSwitch()
  }
  return true
}